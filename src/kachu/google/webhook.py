"""
Google Business Profile review webhook handler.

Google sends Pub/Sub push notifications when a new review is created.
This endpoint receives the notification, extracts the review_id, and
triggers the kachu_review_reply workflow in AgentOS.

Pub/Sub push message format:
  POST /webhooks/google/review
  Body: { "message": { "data": "<base64>", "messageId": "..." }, "subscription": "..." }

The base64-decoded data contains:
  { "reviewId": "...", "locationName": "accounts/*/locations/*" }
"""
from __future__ import annotations

import base64
import binascii
import json
import logging
import secrets
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from google.auth.exceptions import GoogleAuthError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token
from pydantic import ValidationError

from ..agentOS_client import AgentOSClient
from ..models import AgentOSTaskRequest
from ..persistence import KachuRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks/google", tags=["google-webhook"])


def _normalize_google_location(value: str) -> str:
    text = value.strip().strip("/")
    if not text:
        return ""
    if "/locations/" in text:
        return text.rsplit("/", 1)[-1]
    if text.startswith("locations/"):
        return text.split("/", 1)[-1]
    return text


def _is_authorized_google_webhook(settings: Any, authorization: str) -> bool:
    if _verify_google_oidc_token(settings, authorization):
        return True
    expected_secret = getattr(settings, "GOOGLE_WEBHOOK_SHARED_SECRET", "")
    if not expected_secret:
        return settings.APP_ENV == "test"
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and secrets.compare_digest(token.strip(), expected_secret)


def _verify_google_oidc_token(settings: Any, authorization: str) -> bool:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return False

    audience = getattr(settings, "GOOGLE_WEBHOOK_OIDC_AUDIENCE", "")
    if not audience:
        base_url = getattr(settings, "KACHU_BASE_URL", "").rstrip("/")
        if base_url:
            audience = f"{base_url}/webhooks/google/review"
    if not audience:
        return False

    try:
        claims = google_id_token.verify_oauth2_token(
            token.strip(),
            GoogleAuthRequest(),
            audience=audience,
        )
    except (GoogleAuthError, ValueError) as exc:
        logger.warning("Google webhook OIDC verification failed: %s", exc)
        return False

    if claims.get("iss") not in {"accounts.google.com", "https://accounts.google.com"}:
        logger.warning("Google webhook OIDC issuer mismatch: %s", claims.get("iss"))
        return False

    expected_email = getattr(settings, "GOOGLE_WEBHOOK_SERVICE_ACCOUNT_EMAIL", "")
    if expected_email:
        email = claims.get("email", "")
        if email != expected_email or claims.get("email_verified") is not True:
            logger.warning("Google webhook OIDC email mismatch: %s", email)
            return False

    return True


def _resolve_tenant_ids(repo: KachuRepository, settings: Any, location_name: str) -> list[str]:
    event_location_id = _normalize_google_location(location_name)
    default_tenant = getattr(settings, "DEFAULT_TENANT_ID", "")
    configured_location_id = _normalize_google_location(
        getattr(settings, "GOOGLE_BUSINESS_LOCATION_ID", "")
    )

    if default_tenant:
        if configured_location_id and event_location_id and configured_location_id != event_location_id:
            logger.warning(
                "Ignoring Google review webhook due to location mismatch: expected=%s actual=%s",
                configured_location_id,
                event_location_id,
            )
            return []
        return [default_tenant]

    if event_location_id:
        tenant_ids = repo.find_tenant_ids_by_google_location(event_location_id)
        if tenant_ids:
            return tenant_ids

    if configured_location_id and event_location_id and configured_location_id == event_location_id:
        active_tenant_ids = repo.list_active_tenant_ids()
        if len(active_tenant_ids) == 1:
            return active_tenant_ids

    return []


def _repo(request: Request) -> KachuRepository:
    return request.app.state.repository


def _agentOS(request: Request) -> AgentOSClient:
    return request.app.state.agentOS_client


def _settings(request: Request):
    return request.app.state.settings


@router.post("/review")
async def google_review_webhook(
    request: Request,
    authorization: str = Header(default=""),
) -> dict[str, str]:
    """
    Receive Google Pub/Sub push notification for new reviews.
    Triggers kachu_review_reply workflow in AgentOS for all active tenants
    matching this location, or the default tenant if no mapping exists.
    """
    settings = _settings(request)
    if (
        settings.APP_ENV == "production"
        and not settings.GOOGLE_WEBHOOK_SHARED_SECRET
        and not settings.GOOGLE_WEBHOOK_OIDC_AUDIENCE
        and not settings.KACHU_BASE_URL
    ):
        logger.error("Google webhook invoked without an authorization mechanism configured")
        raise HTTPException(status_code=503, detail="Google webhook misconfigured")
    if not _is_authorized_google_webhook(settings, authorization):
        raise HTTPException(status_code=401, detail="Invalid webhook authorization")

    try:
        body: dict[str, Any] = await request.json()
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Extract Pub/Sub message
    message = body.get("message", {})
    raw_data = message.get("data", "")
    message_id = message.get("messageId", "unknown")

    # Decode base64 data
    review_id: str = ""
    location_name: str = ""
    if raw_data:
        try:
            decoded = base64.b64decode(raw_data).decode("utf-8")
            payload = json.loads(decoded)
            review_id = payload.get("reviewId", "")
            location_name = payload.get("locationName", "")
        except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            logger.warning("Failed to decode Pub/Sub message data: %s", exc)

    if not review_id:
        # Google sends verification pings with empty data — acknowledge silently
        logger.info("Google Pub/Sub ping (no review_id), messageId=%s", message_id)
        return {"status": "ok", "note": "ping"}

    logger.info("New review webhook: review_id=%s location=%s", review_id, location_name)

    repo = _repo(request)
    agentOS = _agentOS(request)

    tenant_ids = _resolve_tenant_ids(repo, settings, location_name)
    if not tenant_ids:
        logger.warning(
            "Ignoring Google review webhook with no tenant mapping: review_id=%s location=%s",
            review_id,
            location_name,
        )
        return {"status": "ignored", "reason": "no_tenant_mapping", "review_id": review_id}

    triggered: list[str] = []
    for tenant_id in tenant_ids:
        idempotency_key = f"{tenant_id}:{review_id}"
        try:
            task_view = await agentOS.create_task(AgentOSTaskRequest(
                tenant_id=tenant_id,
                domain="kachu_review_reply",
                objective=f"Reply to Google review {review_id}",
                workflow_input={
                    "tenant_id": tenant_id,
                    "review_id": review_id,
                    "location_name": location_name,
                    "trigger_source": "google_webhook",
                },
                idempotency_key=idempotency_key,
            ))
            await agentOS.run_task(task_view.task["id"])
            triggered.append(tenant_id)
            logger.info("Triggered review_reply for tenant=%s review=%s", tenant_id, review_id)
        except (httpx.HTTPError, ValidationError) as exc:
            logger.error("Failed to trigger review_reply for tenant=%s: %s", tenant_id, exc)

    return {"status": "ok", "triggered": str(len(triggered)), "review_id": review_id}
