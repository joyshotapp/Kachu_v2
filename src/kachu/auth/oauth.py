from __future__ import annotations

import json
import logging
import secrets
import time
from functools import lru_cache
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import RedirectResponse

try:
    from redis import asyncio as redis_asyncio
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover - handled gracefully at runtime
    redis_asyncio = None

    class RedisError(Exception):
        pass

from ..config import Settings, get_settings
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
    code: str = Query(...),
    state: str = Query(...),
    settings: Settings = Depends(_settings),
) -> dict[str, Any]:
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
        # Auto-discover account_id and location_id so tools/router.py can use them directly.
        # GBP API returns resource names like "accounts/12345" and "accounts/12345/locations/67890".
        gbp_account_id = ""
        gbp_location_id = ""
        try:
            import asyncio
            from ..google import GoogleBusinessClient
            temp_client = GoogleBusinessClient.from_oauth_token(access_token)
            loop = asyncio.get_event_loop()
            accounts = await loop.run_in_executor(None, temp_client.list_accounts)
            if accounts:
                gbp_account_id = accounts[0].get("name", "")
                locations = await loop.run_in_executor(
                    None, lambda: temp_client.list_locations(gbp_account_id)
                )
                if locations:
                    gbp_location_id = locations[0].get("name", "")
        except Exception as exc:
            logger.warning("GBP account/location auto-discovery failed: %s", exc)

        gbp_credentials_json = json.dumps(
            {
                "access_token": access_token,
                "refresh_token": token_data.get("refresh_token", ""),
                "expires_in": token_data.get("expires_in", 3600),
                "expires_at": int(time.time()) + int(token_data.get("expires_in", 3600)),
                "scope": token_data.get("scope", ""),
                "token_type": token_data.get("token_type", "Bearer"),
                "account_id": gbp_account_id,
                "location_id": gbp_location_id,
            },
            ensure_ascii=False,
        )
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="google_business",
            credentials_json=gbp_credentials_json,
            account_label="Google Business Profile",
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

    return {
        "status": "connected",
        "tenant_id": tenant_id,
        "platforms": saved_platforms,
    }


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
    return {"tenant_id": tenant_id, "connectors": results}


# ── Meta OAuth ────────────────────────────────────────────────────────────────
_META_AUTH_URL = "https://www.facebook.com/dialog/oauth"
_META_TOKEN_URL = "https://graph.facebook.com/v21.0/oauth/access_token"
_META_SCOPES = [
    "instagram_basic",
    "instagram_content_publish",
    "pages_manage_posts",
    "pages_read_engagement",
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
    code: str = Query(...),
    state: str = Query(...),
    settings: Settings = Depends(_settings),
) -> dict[str, Any]:
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

        # Discover IG User ID via /me/accounts → pages → instagram_business_account
        ig_user_id: str | None = None
        fb_page_id: str | None = None
        try:
            pages_resp = await client.get(
                "https://graph.facebook.com/v21.0/me/accounts",
                params={"access_token": long_token, "fields": "id,name,instagram_business_account"},
            )
            pages_data = pages_resp.json()
            pages = pages_data.get("data", [])
            if pages:
                fb_page_id = pages[0].get("id")
                iba = pages[0].get("instagram_business_account")
                if iba:
                    ig_user_id = iba.get("id")
        except (httpx.HTTPError, json.JSONDecodeError, ValueError, TypeError, KeyError, IndexError) as exc:
            logger.warning("Meta page/IG discovery failed: %s", exc)

    credentials_json = json.dumps(
        {
            "access_token": long_token,
            "ig_user_id": ig_user_id or "",
            "fb_page_id": fb_page_id or "",
            "scope": ",".join(_META_SCOPES),
        },
        ensure_ascii=False,
    )

    repo: KachuRepository = _repo(request)
    repo.save_connector_account(
        tenant_id=tenant_id,
        platform="meta",
        credentials_json=credentials_json,
        account_label=f"Meta (IG:{ig_user_id or '?'} FB:{fb_page_id or '?'})",
    )

    logger.info("Meta OAuth tokens saved for tenant=%s ig=%s fb=%s", tenant_id, ig_user_id, fb_page_id)
    return {
        "status": "connected",
        "tenant_id": tenant_id,
        "platform": "meta",
        "ig_user_id": ig_user_id,
        "fb_page_id": fb_page_id,
    }
