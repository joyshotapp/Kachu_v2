from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from .config import Settings
from .persistence import KachuRepository

_PLAN_CAPABILITIES: dict[str, set[str]] = {
    "trial": {"ga4", "meta", "cross_channel"},
    "starter": {"ga4", "meta"},
    "growth": {"ga4", "meta", "cross_channel"},
    "pro": {"ga4", "meta", "cross_channel", "crm"},
}

_CAPABILITY_LABELS = {
    "ga4": "GA4 analytics",
    "meta": "Meta publishing",
    "cross_channel": "cross-channel publishing",
    "crm": "CRM features",
}


def _coerce_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass
class TenantCapabilityDecision:
    allowed: bool
    reason: str = ""
    plan: str = "trial"
    plan_status: str = "active"


def load_connector_credentials(account: Any) -> dict[str, Any]:
    if account is None or not getattr(account, "credentials_encrypted", ""):
        return {}
    try:
        payload = json.loads(account.credentials_encrypted)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def connector_can_refresh(account: Any) -> bool:
    if account is None or getattr(account, "platform", "") not in {"google_business", "ga4"}:
        return False
    creds = load_connector_credentials(account)
    return bool(creds.get("refresh_token"))


def connector_refresh_state(account: Any) -> dict[str, Any]:
    creds = load_connector_credentials(account)
    refresh_status = str(creds.get("refresh_status", "healthy") or "healthy")
    return {
        "expires_at": creds.get("expires_at"),
        "refresh_status": refresh_status,
        "refresh_error": str(creds.get("last_refresh_error", "") or "").strip() or None,
        "refresh_failed_at": creds.get("last_refresh_failed_at"),
        "can_refresh": connector_can_refresh(account),
        "has_refresh_token": bool(creds.get("refresh_token")),
    }


def evaluate_tenant_capability(
    repo: KachuRepository,
    tenant_id: str,
    capability: str | None = None,
) -> TenantCapabilityDecision:
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        return TenantCapabilityDecision(allowed=False, reason="Tenant not found")
    if not tenant.is_active:
        return TenantCapabilityDecision(allowed=False, reason="Tenant is deactivated", plan=tenant.plan)

    now = datetime.now(timezone.utc)
    plan_status = "active"
    plan_expires_at = _coerce_utc(tenant.plan_expires_at)
    if plan_expires_at and plan_expires_at <= now:
        plan_status = "expired"
        return TenantCapabilityDecision(
            allowed=False,
            reason="Tenant plan has expired",
            plan=tenant.plan,
            plan_status=plan_status,
        )

    if not capability:
        return TenantCapabilityDecision(allowed=True, plan=tenant.plan, plan_status=plan_status)

    flags = repo.get_or_create_tenant_feature_flags(tenant_id)
    feature_field = f"{capability}_enabled"
    if hasattr(flags, feature_field) and not bool(getattr(flags, feature_field)):
        return TenantCapabilityDecision(
            allowed=False,
            reason=f"{_CAPABILITY_LABELS.get(capability, capability)} is disabled for this tenant",
            plan=tenant.plan,
            plan_status=plan_status,
        )

    plan_key = str(tenant.plan or "trial").strip().lower() or "trial"
    entitlements = _PLAN_CAPABILITIES.get(plan_key, _PLAN_CAPABILITIES["trial"])
    if capability not in entitlements:
        return TenantCapabilityDecision(
            allowed=False,
            reason=f"{_CAPABILITY_LABELS.get(capability, capability)} is not included in the {plan_key} plan",
            plan=plan_key,
            plan_status=plan_status,
        )

    return TenantCapabilityDecision(allowed=True, plan=plan_key, plan_status=plan_status)


def refresh_google_connector_credentials(
    repo: KachuRepository,
    settings: Settings,
    *,
    tenant_id: str,
    platform: str,
    force: bool = False,
    source: str = "connector_refresh",
) -> tuple[Any, dict[str, Any], bool]:
    if platform not in {"google_business", "ga4"}:
        raise ValueError("Only google_business and ga4 support token refresh")

    account = repo.get_connector_account(tenant_id, platform)
    if account is None:
        raise ValueError("Connector not found")

    creds = load_connector_credentials(account)
    refresh_token = str(creds.get("refresh_token", "") or "").strip()
    if not refresh_token:
        raise ValueError("No refresh token available; reconnect required")

    expires_at = int(creds.get("expires_at", 0) or 0)
    if not force and expires_at and time.time() <= expires_at - 300:
        creds["refresh_status"] = "healthy"
        return account, creds, False

    response = httpx.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", ""),
            "client_secret": getattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", ""),
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=10,
    )

    if response.status_code != 200:
        error_text = f"HTTP {response.status_code}: {response.text[:200]}"
        creds["refresh_status"] = "failed"
        creds["last_refresh_error"] = error_text
        creds["last_refresh_failed_at"] = datetime.now(timezone.utc).isoformat()
        repo.update_connector_account(
            tenant_id=tenant_id,
            platform=platform,
            credentials_json=json.dumps(creds, ensure_ascii=False),
            touch_refreshed_at=False,
        )
        repo.save_audit_event(
            tenant_id=tenant_id,
            event_type="connector_refresh_failed",
            source=source,
            actor_id="system",
            payload={"platform": platform, "forced": force, "error": error_text},
        )
        raise ValueError(error_text)

    new_data = response.json()
    creds["access_token"] = new_data["access_token"]
    creds["expires_at"] = int(time.time()) + int(new_data.get("expires_in", 3600))
    if new_data.get("refresh_token"):
        creds["refresh_token"] = new_data["refresh_token"]
    creds["refresh_status"] = "healthy"
    creds.pop("last_refresh_error", None)
    creds.pop("last_refresh_failed_at", None)
    creds["last_refreshed_via"] = "forced" if force else "auto"

    refreshed_account = repo.update_connector_account(
        tenant_id=tenant_id,
        platform=platform,
        credentials_json=json.dumps(creds, ensure_ascii=False),
        touch_refreshed_at=True,
    )
    repo.save_audit_event(
        tenant_id=tenant_id,
        event_type="connector_refreshed",
        source=source,
        actor_id="system",
        payload={"platform": platform, "forced": force},
    )
    return refreshed_account, creds, True