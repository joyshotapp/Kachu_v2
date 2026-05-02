from __future__ import annotations

from html import escape
import json
import logging
import secrets
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from redis import asyncio as redis_asyncio
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - handled gracefully at runtime
    redis_asyncio = None

    class RedisError(Exception):
        pass

from ..config import Settings, get_settings
from ..line.push import push_line_messages, text_message
from ..persistence import KachuRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Google OAuth endpoints
_GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Scopes: Google Business Profile + GA4 Analytics
_GBP_SCOPE = "https://www.googleapis.com/auth/business.manage"
_GA4_SCOPE = "https://www.googleapis.com/auth/analytics.readonly"


def _repo(request: Request) -> KachuRepository:
    return request.app.state.repository


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _build_google_connect_url(settings: Settings, tenant_id: str, *, platforms: str = "gbp") -> str:
        base_url = settings.KACHU_BASE_URL.rstrip("/")
        return f"{base_url}/auth/google/connect?{urlencode({'tenant_id': tenant_id, 'platforms': platforms})}"


def _build_meta_connect_url(settings: Settings, tenant_id: str) -> str:
        base_url = settings.KACHU_BASE_URL.rstrip("/")
        return f"{base_url}/auth/meta/connect?{urlencode({'tenant_id': tenant_id})}"


def _load_connector_credentials(account: Any) -> dict[str, Any]:
    if account is None or not getattr(account, "credentials_encrypted", ""):
        return {}
    try:
        return json.loads(account.credentials_encrypted)
    except (json.JSONDecodeError, TypeError):
        return {}


def _build_phase0_readiness(repo: KachuRepository, tenant_id: str) -> dict[str, Any]:
    meta_account = repo.get_connector_account(tenant_id, "meta")
    google_account = repo.get_connector_account(tenant_id, "google_business")
    meta_creds = _load_connector_credentials(meta_account)

    fb_ready = bool(meta_account and meta_account.is_active and meta_creds.get("fb_page_id"))
    ig_ready = bool(meta_account and meta_account.is_active and meta_creds.get("ig_user_id"))
    google_ready = bool(google_account and google_account.is_active)

    channels = {
        "facebook": {
            "connected": fb_ready,
            "status": "ready" if fb_ready else "pending_connection",
            "label": str(meta_creds.get("fb_page_name", "")).strip() or None,
            "note": "已可發布 Facebook 內容" if fb_ready else "尚未完成 Facebook 粉專連結",
        },
        "instagram": {
            "connected": ig_ready,
            "status": "ready" if ig_ready else ("needs_business_link" if fb_ready else "pending_connection"),
            "label": None,
            "note": (
                "已可發布 Instagram 內容"
                if ig_ready
                else "Facebook 已連結，但尚未偵測到 IG 商業帳號"
                if fb_ready
                else "尚未完成 Instagram 連結"
            ),
        },
        "google_business": {
            "connected": google_ready,
            "status": "ready" if google_ready else "pending_connection",
            "label": getattr(google_account, "account_label", "") or None,
            "note": "Google 商家渠道已完成授權" if google_ready else "尚未完成 Google 商家連結",
        },
    }

    ready_channels = [name for name, item in channels.items() if item["connected"]]
    if fb_ready and not ig_ready:
        next_step = "你現在可以先從 Facebook 開始；如果之後要連動 Instagram，再補上 IG 商業帳號連結即可。"
    elif ready_channels:
        channel_names = {
            "facebook": "Facebook",
            "instagram": "Instagram",
            "google_business": "Google 商家",
        }
        next_step = "你現在可以先從「{}」開始。".format(" / ".join(channel_names[name] for name in ready_channels))
    else:
        next_step = "請先完成至少一個渠道連結，完成後我就能帶你開始第一個任務。"

    return {
        "channels": channels,
        "ready_channels": ready_channels,
        "next_step": next_step,
    }


def _build_phase0_readiness_lines(repo: KachuRepository, tenant_id: str) -> list[str]:
    readiness = _build_phase0_readiness(repo, tenant_id)
    label_map = {
        "facebook": "Facebook",
        "instagram": "Instagram",
        "google_business": "Google 商家",
    }
    lines = ["目前渠道狀態："]
    for channel_name in ("facebook", "instagram", "google_business"):
        channel = readiness["channels"][channel_name]
        status_text = "已可用" if channel["connected"] else "尚未就緒"
        lines.append(f"- {label_map[channel_name]}：{status_text}；{channel['note']}")
    lines.append(readiness["next_step"])
    return lines


def _normalize_meta_pages(pages: list[dict[str, Any]]) -> list[dict[str, str]]:
    normalized: list[dict[str, str]] = []
    for page in pages:
        page_id = str(page.get("id", "")).strip()
        if not page_id:
            continue
        page_name = str(page.get("name", "")).strip() or f"Facebook Page {page_id}"
        instagram_business_account = page.get("instagram_business_account") or {}
        ig_user_id = str(instagram_business_account.get("id", "")).strip()
        normalized.append(
            {
                "id": page_id,
                "name": page_name,
                "ig_user_id": ig_user_id,
            }
        )
    return normalized


async def _discover_meta_pages(client: httpx.AsyncClient, access_token: str) -> list[dict[str, str]]:
    discovered_pages: list[dict[str, str]] = []
    seen_page_ids: set[str] = set()
    after_cursor: str | None = None

    while True:
        params: dict[str, str | int] = {
            "access_token": access_token,
            "fields": "id,name,instagram_business_account",
            "limit": 100,
        }
        if after_cursor:
            params["after"] = after_cursor

        pages_resp = await client.get(
            "https://graph.facebook.com/v21.0/me/accounts",
            params=params,
        )
        pages_data = pages_resp.json()
        for page in _normalize_meta_pages(pages_data.get("data", [])):
            page_id = page.get("id", "")
            if not page_id or page_id in seen_page_ids:
                continue
            seen_page_ids.add(page_id)
            discovered_pages.append(page)

        paging = pages_data.get("paging") or {}
        cursors = paging.get("cursors") or {}
        after_cursor = str(cursors.get("after", "")).strip() or None
        if not after_cursor:
            break

    return discovered_pages


async def _lookup_meta_pages_by_ids(
    client: httpx.AsyncClient,
    access_token: str,
    page_ids: list[str],
) -> list[dict[str, str]]:
    if not page_ids:
        return []

    resp = await client.get(
        "https://graph.facebook.com/v21.0/",
        params={
            "access_token": access_token,
            "ids": ",".join(page_ids),
            "fields": "id,name,instagram_business_account",
        },
    )
    payload = resp.json()
    normalized_pages: list[dict[str, str]] = []
    for page_id in page_ids:
        page_payload = payload.get(page_id)
        if not isinstance(page_payload, dict):
            continue
        normalized_pages.extend(_normalize_meta_pages([page_payload]))
    return normalized_pages


async def _discover_meta_pages_from_granular_scopes(
    client: httpx.AsyncClient,
    settings: Settings,
    access_token: str,
) -> list[dict[str, str]]:
    app_id = settings.META_APP_ID.strip()
    app_secret = settings.META_APP_SECRET.strip()
    if not app_id or not app_secret:
        return []

    debug_resp = await client.get(
        "https://graph.facebook.com/debug_token",
        params={
            "input_token": access_token,
            "access_token": f"{app_id}|{app_secret}",
        },
    )
    debug_data = debug_resp.json().get("data", {})
    granular_scopes = debug_data.get("granular_scopes", [])

    ordered_page_ids: list[str] = []
    seen_page_ids: set[str] = set()
    for scope_entry in granular_scopes:
        if scope_entry.get("scope") not in {"pages_show_list", "pages_manage_posts", "pages_read_engagement"}:
            continue
        for target_id in scope_entry.get("target_ids", []):
            page_id = str(target_id).strip()
            if not page_id or page_id in seen_page_ids:
                continue
            seen_page_ids.add(page_id)
            ordered_page_ids.append(page_id)

    return await _lookup_meta_pages_by_ids(client, access_token, ordered_page_ids)


async def _fetch_meta_page_access_token(
    client: httpx.AsyncClient,
    *,
    user_access_token: str,
    page_id: str,
) -> str:
    if not page_id or not user_access_token:
        return ""

    resp = await client.get(
        f"https://graph.facebook.com/v21.0/{page_id}",
        params={
            "access_token": user_access_token,
            "fields": "access_token",
        },
    )
    payload = resp.json()
    return str(payload.get("access_token", "")).strip()


def _save_meta_connector(
    repo: KachuRepository,
    *,
    tenant_id: str,
    access_token: str,
    fb_access_token: str,
    selected_page: dict[str, str],
) -> None:
    fb_page_id = selected_page.get("id", "")
    fb_page_name = selected_page.get("name", "")
    ig_user_id = selected_page.get("ig_user_id", "")
    credentials_json = json.dumps(
        {
            "access_token": access_token,
            "fb_access_token": fb_access_token,
            "ig_user_id": ig_user_id,
            "fb_page_id": fb_page_id,
            "fb_page_name": fb_page_name,
            "scope": ",".join(_META_SCOPES),
        },
        ensure_ascii=False,
    )
    repo.save_connector_account(
        tenant_id=tenant_id,
        platform="meta",
        credentials_json=credentials_json,
        account_label=f"Meta ({fb_page_name or 'Page'} | IG:{ig_user_id or '?'} FB:{fb_page_id or '?'})",
    )


def _build_meta_connected_line_texts(repo: KachuRepository, tenant_id: str, *, page_name: str, has_instagram: bool) -> list[str]:
    messages = [f"Meta 已連結成功。\n\n目前綁定的 Facebook 粉專是：{page_name}。"]
    if has_instagram:
        messages[0] += "\n之後我可以協助你處理 Facebook / Instagram 內容。"
    else:
        messages[0] += "\n目前已可先用於 Facebook；如果之後要連動 Instagram，請先確認這個粉專已連結 IG 商業帳號後再重新授權。"
    messages.extend(_build_phase0_readiness_lines(repo, tenant_id))
    return messages


def _render_meta_page_selection_page(*, selection_token: str, pages: list[dict[str, str]]) -> HTMLResponse:
    page_cards = "".join(
        (
            '<a class="page-card" href="/auth/meta/select-page?'
            + urlencode({"selection_token": selection_token, "page_id": page["id"]})
            + f'">'
            + f'<div class="page-name">{escape(page["name"])}</div>'
            + f'<div class="page-meta">Facebook Page ID: {escape(page["id"])}</div>'
            + (
                '<div class="page-status ready">已連結 Instagram 商業帳號</div>'
                if page.get("ig_user_id")
                else '<div class="page-status missing">尚未偵測到 Instagram 商業帳號</div>'
            )
            + "</a>"
        )
        for page in pages
    )
    html = f"""<!DOCTYPE html>
<html lang=\"zh-Hant\">
    <head>
        <meta charset=\"UTF-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
        <title>選擇要綁定的 Facebook 粉專</title>
        <style>
            :root {{
                color-scheme: light;
                --bg: #f7f2e8;
                --card: #fffdf8;
                --ink: #2d241b;
                --muted: #74685a;
                --accent: #1f7a5a;
                --border: #e6dac9;
                --warning: #b56a1a;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                padding: 24px;
                background: radial-gradient(circle at top, #fff7ea 0%, var(--bg) 60%, #efe4d3 100%);
                color: var(--ink);
                font-family: "Noto Sans TC", "PingFang TC", sans-serif;
            }}
            .card {{
                width: min(100%, 720px);
                padding: 32px 28px;
                border: 1px solid var(--border);
                border-radius: 24px;
                background: var(--card);
                box-shadow: 0 20px 60px rgba(73, 54, 34, 0.10);
            }}
            .eyebrow {{
                display: inline-block;
                margin-bottom: 12px;
                padding: 6px 10px;
                border-radius: 999px;
                background: #eaf5ef;
                color: var(--accent);
                font-size: 13px;
                font-weight: 700;
            }}
            h1 {{ margin: 0 0 12px; font-size: 28px; line-height: 1.25; }}
            p {{ margin: 0 0 12px; color: var(--muted); font-size: 16px; line-height: 1.7; }}
            .page-list {{ display: grid; gap: 12px; margin-top: 20px; }}
            .page-card {{
                display: block;
                padding: 16px 18px;
                border: 1px solid var(--border);
                border-radius: 16px;
                background: #fff;
                color: inherit;
                text-decoration: none;
            }}
            .page-card:hover {{ border-color: var(--accent); box-shadow: 0 10px 24px rgba(31, 122, 90, 0.10); }}
            .page-name {{ font-size: 18px; font-weight: 700; margin-bottom: 6px; }}
            .page-meta {{ color: var(--muted); font-size: 14px; margin-bottom: 8px; }}
            .page-status {{ font-size: 14px; font-weight: 700; }}
            .ready {{ color: var(--accent); }}
            .missing {{ color: var(--warning); }}
        </style>
    </head>
    <body>
        <main class=\"card\">
            <div class=\"eyebrow\">Kachu Meta 授權</div>
            <h1>選擇要綁定的 Facebook 粉專</h1>
            <p>你這個 Meta 帳號底下有多個粉專。請選擇這次要交給 Kachu 使用的那一個。</p>
            <p>如果某個粉專沒有顯示 Instagram 商業帳號，代表目前只能先用 Facebook 發布。</p>
            <div class=\"page-list\">{page_cards}</div>
        </main>
    </body>
</html>
"""
    return HTMLResponse(content=html)


def _render_oauth_success_page(*, title: str, paragraphs: list[str]) -> HTMLResponse:
        paragraph_html = "".join(f"<p>{escape(paragraph)}</p>" for paragraph in paragraphs)
        html = f"""<!DOCTYPE html>
<html lang=\"zh-Hant\">
    <head>
        <meta charset=\"UTF-8\" />
        <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\" />
        <title>{escape(title)}</title>
        <style>
            :root {{
                color-scheme: light;
                --bg: #f7f2e8;
                --card: #fffdf8;
                --ink: #2d241b;
                --muted: #74685a;
                --accent: #1f7a5a;
                --border: #e6dac9;
            }}
            * {{ box-sizing: border-box; }}
            body {{
                margin: 0;
                min-height: 100vh;
                display: grid;
                place-items: center;
                padding: 24px;
                background: radial-gradient(circle at top, #fff7ea 0%, var(--bg) 60%, #efe4d3 100%);
                color: var(--ink);
                font-family: "Noto Sans TC", "PingFang TC", sans-serif;
            }}
            .card {{
                width: min(100%, 560px);
                padding: 32px 28px;
                border: 1px solid var(--border);
                border-radius: 24px;
                background: var(--card);
                box-shadow: 0 20px 60px rgba(73, 54, 34, 0.10);
            }}
            .eyebrow {{
                display: inline-block;
                margin-bottom: 12px;
                padding: 6px 10px;
                border-radius: 999px;
                background: #eaf5ef;
                color: var(--accent);
                font-size: 13px;
                font-weight: 700;
            }}
            h1 {{ margin: 0 0 12px; font-size: 28px; line-height: 1.25; }}
            p {{ margin: 0 0 12px; color: var(--muted); font-size: 16px; line-height: 1.7; }}
            .hint {{ margin-top: 20px; color: var(--ink); font-weight: 700; }}
        </style>
    </head>
    <body>
        <main class=\"card\">
            <div class=\"eyebrow\">Kachu 串接完成</div>
            <h1>{escape(title)}</h1>
            {paragraph_html}
            <p class=\"hint\">現在可以直接關閉此頁面，回到 LINE 繼續使用 Kachu。</p>
        </main>
    </body>
</html>
"""
        return HTMLResponse(content=html)


async def _push_line_texts(settings: Settings, tenant_id: str, texts: list[str]) -> None:
        if not tenant_id or not settings.LINE_CHANNEL_ACCESS_TOKEN or not texts:
                return

        try:
                await push_line_messages(
                        to=tenant_id,
                        messages=[text_message(text) for text in texts],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
        except httpx.HTTPError as exc:
                logger.warning("Failed to push OAuth completion message to LINE for tenant=%s: %s", tenant_id, exc)


def _build_google_connected_line_texts(settings: Settings, repo: KachuRepository, tenant_id: str, platforms: list[str]) -> list[str]:
    if "google_business" in platforms and "ga4" in platforms:
        summary = "Google 授權已完成。"
    elif "google_business" in platforms:
        summary = "Google 授權已完成。"
    else:
        summary = "GA4 授權已完成。"

    messages = [summary]
    if "google_business" in platforms:
        messages[0] += "\n\nKachu 已經收到你的 Google 授權。Google 商家功能仍在開發端審批流程中，正式開放後才會提供給使用者。"
        messages.append(
            "在正式開放前，這次授權會保留作為內部驗證資料。若之後要進一步驗證 Facebook / Instagram，會再由內部測試流程另行安排。"
        )
    elif "ga4" in platforms:
        messages[0] += "\n\nGA4 相關功能完成正式開放後，才會提供給使用者。"

    messages.extend(_build_phase0_readiness_lines(repo, tenant_id))

    return messages


def _build_google_connector_credentials(
    token_data: dict[str, Any],
    *,
    account_id: str = "",
    location_id: str = "",
) -> str:
    return json.dumps(
        {
            "access_token": token_data.get("access_token", ""),
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_in": token_data.get("expires_in", 3600),
            "expires_at": int(time.time()) + int(token_data.get("expires_in", 3600)),
            "scope": token_data.get("scope", ""),
            "token_type": token_data.get("token_type", "Bearer"),
            "account_id": account_id,
            "location_id": location_id,
        },
        ensure_ascii=False,
    )


def _save_google_business_connector(
    repo: KachuRepository,
    *,
    tenant_id: str,
    token_data: dict[str, Any],
    account_label: str,
    account_id: str = "",
    location_id: str = "",
) -> None:
    repo.save_connector_account(
        tenant_id=tenant_id,
        platform="google_business",
        credentials_json=_build_google_connector_credentials(
            token_data,
            account_id=account_id,
            location_id=location_id,
        ),
        account_label=account_label,
    )


def _backfill_google_business_connector(
    repo: KachuRepository,
    *,
    tenant_id: str,
    token_data: dict[str, Any],
    account_label: str,
) -> None:
    access_token = str(token_data.get("access_token", "")).strip()
    if not access_token:
        logger.warning("Skip GBP backfill: missing access token for tenant=%s", tenant_id)
        return

    try:
        from ..google import GoogleBusinessClient

        temp_client = GoogleBusinessClient.from_oauth_token(access_token)
        accounts = temp_client.list_accounts()
        if not accounts:
            logger.warning("GBP account discovery returned no accounts for tenant=%s", tenant_id)
            return

        account_id = str(accounts[0].get("name", "")).strip()
        locations = temp_client.list_locations(account_id) if account_id else []
        if not locations:
            logger.warning("GBP location discovery returned no locations for tenant=%s", tenant_id)
            return

        location_id = str(locations[0].get("name", "")).strip()
        _save_google_business_connector(
            repo,
            tenant_id=tenant_id,
            token_data=token_data,
            account_label=account_label,
            account_id=account_id,
            location_id=location_id,
        )
        logger.info(
            "GBP discovery backfill completed for tenant=%s account_id=%s location_id=%s",
            tenant_id,
            account_id,
            location_id,
        )
    except Exception as exc:
        logger.warning("GBP account/location backfill failed for tenant=%s: %s", tenant_id, exc)


# ── In-memory state store (dev-only; use Redis in production) ─────────────────
# Maps state_token -> {"tenant_id": ..., "platforms": [...]}
_pending_states: dict[str, dict[str, Any]] = {}
_STATE_TTL_SECONDS = 600.0
_STATE_KEY_PREFIX = "kachu:oauth_state:"


def _cleanup_expired_pending_states(now: float | None = None) -> None:
    current_time = now if now is not None else time.time()
    expired_tokens = [
        token
        for token, state_data in _pending_states.items()
        if current_time - float(state_data.get("_created_at", current_time)) > _STATE_TTL_SECONDS
    ]
    for token in expired_tokens:
        _pending_states.pop(token, None)


def _store_pending_state_memory(state_token: str, payload: dict[str, Any], ttl_seconds: float) -> None:
    _cleanup_expired_pending_states()
    _pending_states[state_token] = {
        **payload,
        "_created_at": time.time(),
        "_ttl_seconds": ttl_seconds,
    }


def _pop_pending_state_memory(state_token: str) -> dict[str, Any] | None:
    _cleanup_expired_pending_states()
    state_data = _pending_states.pop(state_token, None)
    if state_data is None:
        return None

    created_at = float(state_data.get("_created_at", 0.0))
    ttl_seconds = float(state_data.get("_ttl_seconds", _STATE_TTL_SECONDS))
    if time.time() - created_at > ttl_seconds:
        return None

    return {
        key: value
        for key, value in state_data.items()
        if key not in {"_created_at", "_ttl_seconds"}
    }


def _get_state_ttl_seconds(settings: Settings) -> int:
    return max(int(getattr(settings, "OAUTH_STATE_TTL_SECONDS", _STATE_TTL_SECONDS)), 1)


def _should_use_redis_state_store(settings: Settings) -> bool:
    backend = getattr(settings, "OAUTH_STATE_STORE_BACKEND", "auto").lower()
    if backend == "redis":
        return True
    if backend == "memory":
        return False
    return settings.APP_ENV == "production"


@lru_cache(maxsize=4)
def _build_redis_state_client(redis_url: str):
    if redis_asyncio is None:
        return None
    return redis_asyncio.from_url(redis_url, encoding="utf-8", decode_responses=True)


def _get_redis_state_client(settings: Settings):
    if not _should_use_redis_state_store(settings):
        return None
    redis_url = settings.REDIS_URL.strip()
    if not redis_url:
        raise RuntimeError("OAuth state store requires REDIS_URL when Redis backend is enabled")
    client = _build_redis_state_client(redis_url)
    if client is None:
        raise RuntimeError("OAuth state store requires the redis package for shared state")
    return client


def _state_store_requires_redis(settings: Settings) -> bool:
    return settings.APP_ENV == "production" or settings.OAUTH_STATE_STORE_BACKEND.lower() == "redis"


async def _store_pending_state(
    settings: Settings,
    state_token: str,
    payload: dict[str, Any],
) -> None:
    ttl_seconds = _get_state_ttl_seconds(settings)
    redis_client = _get_redis_state_client(settings)
    if redis_client is not None:
        try:
            await redis_client.set(
                f"{_STATE_KEY_PREFIX}{state_token}",
                json.dumps(payload, ensure_ascii=False),
                ex=ttl_seconds,
            )
            return
        except RedisError as exc:
            if _state_store_requires_redis(settings):
                raise RuntimeError("OAuth state store unavailable") from exc
            logger.warning("Redis-backed OAuth state store unavailable, falling back to memory: %s", exc)

    _store_pending_state_memory(state_token, payload, ttl_seconds)


async def _pop_pending_state(settings: Settings, state_token: str) -> dict[str, Any] | None:
    redis_client = _get_redis_state_client(settings)
    if redis_client is not None:
        try:
            raw_state = await redis_client.getdel(f"{_STATE_KEY_PREFIX}{state_token}")
        except RedisError as exc:
            if _state_store_requires_redis(settings):
                raise RuntimeError("OAuth state store unavailable") from exc
            logger.warning("Redis-backed OAuth state fetch failed, falling back to memory: %s", exc)
        else:
            if not raw_state:
                return None
            try:
                payload = json.loads(raw_state)
            except json.JSONDecodeError:
                logger.warning("OAuth state payload in Redis was invalid JSON for state=%s", state_token)
                return None
            return payload if isinstance(payload, dict) else None

    return _pop_pending_state_memory(state_token)


@router.get("/google/connect")
async def google_connect(
    tenant_id: str = Query(...),
    platforms: str = Query(default="gbp,ga4",
                           description="Comma-separated: gbp | ga4 | both"),
    settings: Settings = Depends(_settings),
) -> RedirectResponse:
    """
    Step 1: Redirect boss to Google OAuth consent screen.

    Requests both GBP and GA4 scopes in one flow so the boss only authorises once.
    After consent, Google redirects to /auth/google/callback.
    """
    if not settings.GOOGLE_OAUTH_CLIENT_ID:
        raise HTTPException(status_code=503, detail="Google OAuth not configured")

    requested_platforms = [p.strip() for p in platforms.split(",")]
    scopes = ["openid", "email"]
    if "gbp" in requested_platforms:
        scopes.append(_GBP_SCOPE)
    if "ga4" in requested_platforms:
        scopes.append(_GA4_SCOPE)

    # CSRF state token
    state_token = secrets.token_urlsafe(32)
    try:
        await _store_pending_state(
            settings,
            state_token,
            {
                "tenant_id": tenant_id,
                "platforms": requested_platforms,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    redirect_uri = settings.GOOGLE_REDIRECT_URI or f"{settings.KACHU_BASE_URL}/auth/google/callback"
    params = {
        "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state_token,
        "access_type": "offline",
        "prompt": "consent",
    }
    return RedirectResponse(url=f"{_GOOGLE_AUTH_URL}?{urlencode(params)}")


@router.get("/google/callback")
async def google_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = Query(...),
    state: str = Query(...),
    settings: Settings = Depends(_settings),
) -> HTMLResponse:
    """
    Step 2: Exchange auth code for tokens and store in ConnectorAccountTable.

    Returns a simple confirmation; in a real app this would redirect to a success page.
    """
    try:
        state_data = await _pop_pending_state(settings, state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if state_data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    tenant_id: str = state_data["tenant_id"]
    platforms: list[str] = state_data["platforms"]

    redirect_uri = settings.GOOGLE_REDIRECT_URI or f"{settings.KACHU_BASE_URL}/auth/google/callback"

    # Exchange code for tokens
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            _GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            timeout=15.0,
        )
        if resp.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Token exchange failed: {resp.text}",
            )
        token_data = resp.json()

    access_token = token_data.get("access_token", "")

    repo: KachuRepository = _repo(request)
    saved_platforms = []

    if "gbp" in platforms:
        account_label = "Google Business Profile"
        _save_google_business_connector(
            repo,
            tenant_id=tenant_id,
            token_data=token_data,
            account_label=account_label,
        )
        background_tasks.add_task(
            _backfill_google_business_connector,
            repo,
            tenant_id=tenant_id,
            token_data=token_data,
            account_label=account_label,
        )
        saved_platforms.append("google_business")

    credentials_json = json.dumps(
        {
            "access_token": access_token,
            "refresh_token": token_data.get("refresh_token", ""),
            "expires_in": token_data.get("expires_in", 3600),
            "scope": token_data.get("scope", ""),
            "token_type": token_data.get("token_type", "Bearer"),
        },
        ensure_ascii=False,
    )

    if "ga4" in platforms:
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="ga4",
            credentials_json=credentials_json,
            account_label="Google Analytics 4",
        )
        saved_platforms.append("ga4")

    logger.info("OAuth tokens saved for tenant=%s platforms=%s", tenant_id, saved_platforms)

    background_tasks.add_task(
        _push_line_texts,
        settings,
        tenant_id,
        _build_google_connected_line_texts(settings, repo, tenant_id, saved_platforms),
    )

    title = "Google 授權已完成"
    if saved_platforms == ["google_business"]:
        title = "Google 商家授權已完成"
    elif saved_platforms == ["ga4"]:
        title = "GA4 授權已完成"

    paragraphs = [
        "Kachu 已經收到授權，不需要登入任何 Kachu 後台。",
    ]
    if "google_business" in saved_platforms:
        paragraphs.append("這次授權屬於內部驗證流程，用來確認 Google 串接鏈路正常。")
        paragraphs.append("Google 商家功能仍待開發端審批完成後，才會正式開放給使用者。")
    elif "ga4" in saved_platforms:
        paragraphs.append("這次授權屬於內部驗證流程。GA4 功能正式開放後，才會提供給使用者。")

    return _render_oauth_success_page(title=title, paragraphs=paragraphs)


@router.get("/status/{tenant_id}")
async def connector_status(
    tenant_id: str,
    request: Request,
) -> dict[str, Any]:
    """Check which platforms are currently connected for a tenant."""
    repo: KachuRepository = _repo(request)
    results = {}
    for platform in ("google_business", "ga4", "meta"):
        account = repo.get_connector_account(tenant_id, platform)
        results[platform] = {
            "connected": account is not None and account.is_active,
            "last_refreshed_at": account.last_refreshed_at.isoformat() if account and account.last_refreshed_at else None,
        }
    return {
        "tenant_id": tenant_id,
        "connectors": results,
        "readiness": _build_phase0_readiness(repo, tenant_id),
    }


# ── Meta OAuth ────────────────────────────────────────────────────────────────
_META_AUTH_URL = "https://www.facebook.com/dialog/oauth"
_META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
_META_SCOPES = [
    "instagram_content_publish",
    "pages_manage_posts",
    "pages_read_engagement",
    "read_insights",
    "pages_manage_engagement",
    "instagram_manage_comments",
]


@router.get("/meta/connect")
async def meta_connect(
    tenant_id: str = Query(...),
    settings: Settings = Depends(_settings),
) -> RedirectResponse:
    """
    Step 1: Redirect boss to Meta/Facebook OAuth consent screen.
    """
    if not settings.META_APP_ID:
        raise HTTPException(status_code=503, detail="Meta App not configured (META_APP_ID missing)")

    state_token = secrets.token_urlsafe(32)
    try:
        await _store_pending_state(
            settings,
            state_token,
            {"tenant_id": tenant_id, "platforms": ["meta"]},
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    redirect_uri = f"{settings.KACHU_BASE_URL}/auth/meta/callback"
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ",".join(_META_SCOPES),
        "state": state_token,
    }
    return RedirectResponse(url=f"{_META_AUTH_URL}?{urlencode(params)}")


@router.get("/meta/callback")
async def meta_callback(
    request: Request,
    background_tasks: BackgroundTasks,
    code: str = Query(...),
    state: str = Query(...),
    settings: Settings = Depends(_settings),
) -> HTMLResponse:
    """
    Step 2: Exchange auth code for a long-lived access token and store in ConnectorAccountTable.
    Also discovers the IG User ID and FB Page ID for the tenant.
    """
    try:
        state_data = await _pop_pending_state(settings, state)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if state_data is None:
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state")

    tenant_id: str = state_data["tenant_id"]
    redirect_uri = f"{settings.KACHU_BASE_URL}/auth/meta/callback"

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Exchange short-lived code for short-lived token
        token_resp = await client.get(
            _META_TOKEN_URL,
            params={
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        )
        if token_resp.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Meta token exchange failed: {token_resp.text}")
        token_data = token_resp.json()
        short_token = token_data.get("access_token", "")

        # Exchange for long-lived token (60-day)
        long_token_resp = await client.get(
            "https://graph.facebook.com/v21.0/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "fb_exchange_token": short_token,
            },
        )
        if long_token_resp.status_code == 200:
            long_token = long_token_resp.json().get("access_token", short_token)
        else:
            long_token = short_token  # fallback if exchange fails

        normalized_pages: list[dict[str, str]] = []
        try:
            normalized_pages = await _discover_meta_pages_from_granular_scopes(client, settings, long_token)
            if not normalized_pages:
                normalized_pages = await _discover_meta_pages(client, long_token)
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError, KeyError, IndexError) as exc:
            logger.warning("Meta page/IG discovery failed: %s", exc)

    repo: KachuRepository = _repo(request)
    if not normalized_pages:
        return _render_oauth_success_page(
            title="Meta 授權已完成",
            paragraphs=[
                "Kachu 已經收到你的 Meta 授權。",
                "但這個 Meta 帳號下目前沒有可管理的 Facebook 粉專，請確認是否使用正確帳號登入。",
            ],
        )

    if len(normalized_pages) == 1:
        selected_page = normalized_pages[0]
        page_access_token = ""
        try:
            page_access_token = await _fetch_meta_page_access_token(
                client,
                user_access_token=long_token,
                page_id=selected_page.get("id", ""),
            )
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
            logger.warning("Meta page access token discovery failed for page=%s: %s", selected_page.get("id"), exc)
        _save_meta_connector(
            repo,
            tenant_id=tenant_id,
            access_token=long_token,
            fb_access_token=page_access_token,
            selected_page=selected_page,
        )
        logger.info(
            "Meta OAuth tokens saved for tenant=%s page=%s ig=%s fb=%s",
            tenant_id,
            selected_page.get("name"),
            selected_page.get("ig_user_id") or None,
            selected_page.get("id") or None,
        )
        background_tasks.add_task(
            _push_line_texts,
            settings,
            tenant_id,
            _build_meta_connected_line_texts(
                repo,
                tenant_id,
                page_name=selected_page.get("name", "Facebook 粉專"),
                has_instagram=bool(selected_page.get("ig_user_id")),
            ),
        )
        paragraphs = [
            "Kachu 已經收到 Meta 授權，不需要登入任何 Kachu 後台。",
            f"目前綁定的 Facebook 粉專是：{selected_page.get('name', 'Facebook 粉專')}。",
        ]
        if selected_page.get("ig_user_id"):
            paragraphs.append("這個粉專已連結 Instagram 商業帳號，之後可直接處理 Facebook / Instagram 內容。")
        else:
            paragraphs.append("這個粉專目前尚未偵測到 Instagram 商業帳號，因此現在先只能處理 Facebook 發布。")
        return _render_oauth_success_page(title="Meta 已連結成功", paragraphs=paragraphs)

    selection_token = secrets.token_urlsafe(32)
    try:
        await _store_pending_state(
            settings,
            selection_token,
            {
                "tenant_id": tenant_id,
                "meta_long_token": long_token,
                "meta_pages": normalized_pages,
            },
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _render_meta_page_selection_page(selection_token=selection_token, pages=normalized_pages)


@router.get("/meta/select-page")
async def meta_select_page(
    request: Request,
    background_tasks: BackgroundTasks,
    selection_token: str = Query(...),
    page_id: str = Query(...),
    settings: Settings = Depends(_settings),
) -> HTMLResponse:
    try:
        selection_data = await _pop_pending_state(settings, selection_token)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if selection_data is None:
        raise HTTPException(status_code=400, detail="Meta page selection expired or invalid")

    tenant_id = str(selection_data.get("tenant_id", "")).strip()
    long_token = str(selection_data.get("meta_long_token", "")).strip()
    pages = selection_data.get("meta_pages", [])
    selected_page = next((page for page in pages if page.get("id") == page_id), None)
    if not tenant_id or not long_token or not selected_page:
        raise HTTPException(status_code=400, detail="Selected Meta page is invalid")

    repo: KachuRepository = _repo(request)
    page_access_token = ""
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            page_access_token = await _fetch_meta_page_access_token(
                client,
                user_access_token=long_token,
                page_id=selected_page.get("id", ""),
            )
    except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError, KeyError) as exc:
        logger.warning("Meta page access token discovery failed for page=%s: %s", selected_page.get("id"), exc)
    _save_meta_connector(
        repo,
        tenant_id=tenant_id,
        access_token=long_token,
        fb_access_token=page_access_token,
        selected_page=selected_page,
    )
    logger.info(
        "Meta page selected for tenant=%s page=%s ig=%s fb=%s",
        tenant_id,
        selected_page.get("name"),
        selected_page.get("ig_user_id") or None,
        selected_page.get("id") or None,
    )
    background_tasks.add_task(
        _push_line_texts,
        settings,
        tenant_id,
        _build_meta_connected_line_texts(
            repo,
            tenant_id,
            page_name=selected_page.get("name", "Facebook 粉專"),
            has_instagram=bool(selected_page.get("ig_user_id")),
        ),
    )

    paragraphs = [
        "Kachu 已經收到 Meta 授權，不需要登入任何 Kachu 後台。",
        f"你這次選擇綁定的 Facebook 粉專是：{selected_page.get('name', 'Facebook 粉專')}。",
    ]
    if selected_page.get("ig_user_id"):
        paragraphs.append("這個粉專已連結 Instagram 商業帳號，之後可直接處理 Facebook / Instagram 內容。")
    else:
        paragraphs.append("這個粉專目前尚未偵測到 Instagram 商業帳號，因此現在先只能處理 Facebook 發布。")
    return _render_oauth_success_page(title="Meta 已連結成功", paragraphs=paragraphs)
