from __future__ import annotations

from contextlib import contextmanager
import threading
import time
from typing import Any

from kachu_plus.config import get_settings
from kachu_plus.google_business import GoogleBusinessConnectorError, GoogleReviewService
from kachu_plus.meta import MetaConnectorError, resolve_meta_graph_client

_PUBLISH_LOCKS: dict[str, threading.Lock] = {}
_LAST_PUBLISH_COMPLETED_AT: dict[str, float] = {}


def _provider_min_interval_seconds(provider: str) -> float:
    settings = get_settings()
    if provider == "meta":
        raw = float(getattr(settings, "META_GRAPH_MIN_INTERVAL_SECONDS", 0.0) or 0.0)
    elif provider == "google_business":
        raw = float(getattr(settings, "GOOGLE_BUSINESS_MIN_INTERVAL_SECONDS", 0.0) or 0.0)
    else:
        raw = 0.0
    return max(raw, 0.0)


@contextmanager
def _throttle_provider(provider: str, throttle_key: str):
    min_interval = _provider_min_interval_seconds(provider)
    if min_interval <= 0:
        yield
        return

    lock_key = f"{provider}:{throttle_key}"
    lock = _PUBLISH_LOCKS.setdefault(lock_key, threading.Lock())
    with lock:
        last_completed_at = _LAST_PUBLISH_COMPLETED_AT.get(lock_key)
        if last_completed_at is not None:
            remaining = min_interval - (time.monotonic() - last_completed_at)
            if remaining > 0:
                time.sleep(remaining)
        yield
        _LAST_PUBLISH_COMPLETED_AT[lock_key] = time.monotonic()


def publish_content_bundle(
    *,
    repo: Any,
    review_service: GoogleReviewService,
    tenant_id: str,
    run_id: str,
    drafts: dict[str, Any],
    selected_platforms: list[str] | None = None,
    workflow_type: str = "kachu_photo_content",
) -> dict[str, Any]:
    selected = selected_platforms or drafts.get("selected_platforms") or ["ig_fb", "google"]
    results: dict[str, Any] = {}

    if "google" in selected:
        google_text = str(drafts.get("google", "") or "")
        if google_text:
            try:
                client, account_id, location_id = review_service.resolve_client_context(tenant_id)
                with _throttle_provider("google_business", f"{tenant_id}:{account_id}:{location_id}"):
                    result = client.create_local_post(account_id=account_id, location_id=location_id, summary=google_text)
                repo.record_published_content(
                    tenant_id=tenant_id,
                    workflow_type=workflow_type,
                    channel="google_business",
                    source_id=run_id,
                    source_ref=str(result.get("name", "")),
                    content_text=google_text,
                    payload=result,
                )
                results["google"] = {"status": "published", "post_name": result.get("name", "")}
            except GoogleBusinessConnectorError as exc:
                results["google"] = {"status": "failed", "error": str(exc)}

    if "ig_fb" in selected:
        caption = str(drafts.get("ig_fb", "") or "")
        image_url = str(drafts.get("image_url") or drafts.get("photo_url") or "")
        try:
            client, credentials = resolve_meta_graph_client(repo=repo, tenant_id=tenant_id)
            throttle_key = str(credentials.get("fb_page_id", "") or credentials.get("ig_user_id", "") or tenant_id)
            with _throttle_provider("meta", throttle_key):
                if image_url:
                    ig_result = client.post_ig_photo(image_url=image_url, caption=caption) if credentials.get("ig_user_id") else client.post_ig_text(caption=caption)
                    fb_result = client.post_fb_photo(image_url=image_url, message=caption)
                else:
                    ig_result = client.post_ig_text(caption=caption)
                    fb_result = client.post_fb_text(message=caption)
            repo.record_published_content(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                channel="meta",
                source_id=run_id,
                source_ref=str(fb_result.get("fb_post_id") or ig_result.get("ig_media_id") or ""),
                content_text=caption,
                payload={"facebook": fb_result, "instagram": ig_result},
            )
            results["ig_fb"] = {"status": "published", "facebook": fb_result, "instagram": ig_result}
        except MetaConnectorError as exc:
            results["ig_fb"] = {"status": "failed", "error": str(exc)}

    return results


def publish_content_bundle_succeeded(results: dict[str, Any]) -> bool:
    if not results:
        return False
    return all(str(item.get("status", "") or "") == "published" for item in results.values() if isinstance(item, dict))


def publish_review_reply(
    *,
    repo: Any,
    review_service: GoogleReviewService,
    tenant_id: str,
    run_id: str,
    review_id: str,
    reply_text: str,
    workflow_type: str = "kachu_review_reply",
) -> dict[str, Any]:
    if not reply_text:
        return {"status": "skipped", "reason": "empty reply", "review_id": review_id}
    try:
        client, account_id, location_id = review_service.resolve_client_context(tenant_id)
        with _throttle_provider("google_business", f"{tenant_id}:{account_id}:{location_id}"):
            result = client.post_reply(account_id=account_id, location_id=location_id, review_id=review_id, reply_text=reply_text)
        status = "posted"
        payload = result
    except GoogleBusinessConnectorError as exc:
        status = "failed"
        payload = {"status": "failed", "error": str(exc)}
    else:
        repo.record_published_content(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            channel="google_business_review",
            source_id=review_id,
            source_ref=run_id,
            content_text=reply_text,
            payload=payload,
        )
    return {"status": status, "review_id": review_id, "payload": payload}


def publish_meta_reply(
    *,
    repo: Any,
    tenant_id: str,
    run_id: str,
    engagement: Any,
    reply_text: str,
) -> dict[str, Any]:
    if not reply_text:
        return {"status": "skipped", "reason": "empty reply"}
    try:
        client, credentials = resolve_meta_graph_client(repo=repo, tenant_id=tenant_id)
        if engagement.engagement_type == "comment":
            with _throttle_provider("meta", str(credentials.get("fb_page_id", "") or tenant_id)):
                payload = client.reply_to_comment(comment_id=engagement.external_message_id, message=reply_text)
            channel = "meta_comment_reply"
            source_id = engagement.external_message_id
        else:
            recipient_id = str(engagement.author_id or engagement.external_thread_id or "").strip()
            if not recipient_id:
                raise MetaConnectorError("meta message recipient missing")
            with _throttle_provider("meta", str(credentials.get("fb_page_id", "") or tenant_id)):
                payload = client.send_page_message(recipient_id=recipient_id, message=reply_text)
            channel = "meta_message_reply"
            source_id = engagement.external_thread_id or engagement.external_message_id
        repo.record_published_content(
            tenant_id=tenant_id,
            workflow_type="kachu_meta_reply",
            channel=channel,
            source_id=str(source_id),
            source_ref=run_id,
            content_text=reply_text,
            payload=payload,
        )
        return {"status": "posted", "payload": payload}
    except MetaConnectorError as exc:
        return {"status": "failed", "error": str(exc)}