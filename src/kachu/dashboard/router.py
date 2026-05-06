from __future__ import annotations

from datetime import datetime, timezone
import json
import pathlib
import secrets
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import Response
from pydantic import BaseModel

from ..merchant_pages import build_merchant_page_template, load_merchant_page_payload, normalize_merchant_slug, save_merchant_page_payload
from ..persistence import KachuRepository
from ..tenant_runtime import (
    connector_refresh_state,
    evaluate_tenant_capability,
    refresh_google_connector_credentials,
)


def _require_dashboard_access(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    settings = getattr(request.app.state, "settings", None)
    expected_token = (getattr(settings, "ADMIN_SERVICE_TOKEN", "") or "").strip()
    app_env = getattr(settings, "APP_ENV", "development")

    if not expected_token:
        if app_env == "test":
            return
        raise HTTPException(status_code=503, detail="Dashboard auth not configured")

    scheme, _, token = authorization.partition(" ")
    header_token = token.strip() if scheme.lower() == "bearer" else ""
    allow_query_token = request.method == "GET" and request.url.path.rstrip("/") == "/dashboard"
    query_token = request.query_params.get("token", "").strip() if allow_query_token else ""
    presented_token = header_token or query_token

    if not presented_token or not secrets.compare_digest(presented_token, expected_token):
        raise HTTPException(status_code=401, detail="Invalid dashboard authorization")


dashboard_router = APIRouter(
    prefix="/dashboard",
    tags=["dashboard"],
    dependencies=[Depends(_require_dashboard_access)],
)

_STATIC_DIR = pathlib.Path(__file__).parent.parent / "static"


# ── HTML page ────────────────────────────────────────────────────────────────


@dashboard_router.get("", include_in_schema=False)
@dashboard_router.get("/", include_in_schema=False)
def dashboard_home() -> FileResponse:
    """Serve the dashboard SPA HTML."""
    return FileResponse(_STATIC_DIR / "dashboard.html", media_type="text/html")


# ── Helpers ───────────────────────────────────────────────────────────────────


def _repo(request: Request) -> KachuRepository:
    return request.app.state.repository


def _tid(request: Request, tenant_id: str | None) -> str | None:
    """Require explicit tenant_id for dashboard API operations."""
    normalized_tenant_id = (tenant_id or "").strip()
    if normalized_tenant_id:
        return normalized_tenant_id
    raise HTTPException(status_code=400, detail="tenant_id is required")


def _run_to_dict(r: Any) -> dict:
    return {
        "id": r.id,
        "tenant_id": r.tenant_id,
        "agentos_run_id": r.agentos_run_id,
        "agentos_task_id": r.agentos_task_id,
        "workflow_type": r.workflow_type,
        "trigger_source": r.trigger_source,
        "trigger_payload": _safe_json(r.trigger_payload),
        "status": r.status,
        "error_message": r.error_message,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "completed_at": r.completed_at.isoformat() if r.completed_at else None,
    }


def _approval_to_dict(a: Any) -> dict:
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "agentos_run_id": a.agentos_run_id,
        "workflow_type": a.workflow_type,
        "draft_content": _safe_json(a.draft_content),
        "status": a.status,
        "decision": a.decision,
        "priority": a.priority,
        "actor_line_id": a.actor_line_id,
        "created_at": a.created_at.isoformat() if a.created_at else None,
        "decided_at": a.decided_at.isoformat() if a.decided_at else None,
        "expires_at": a.expires_at.isoformat() if a.expires_at else None,
    }


def _knowledge_to_dict(e: Any) -> dict:
    timestamps = [
        _normalize_dashboard_datetime(getattr(e, "updated_at", None)),
        _normalize_dashboard_datetime(getattr(e, "last_retrieved_at", None)),
        _normalize_dashboard_datetime(getattr(e, "last_reviewed_at", None)),
    ]
    timestamps = [timestamp for timestamp in timestamps if timestamp is not None]
    activity_at = max(timestamps) if timestamps else None
    activity_age_days = None
    if activity_at is not None:
        activity_age_days = max(int((datetime.now(timezone.utc) - activity_at).total_seconds() // 86400), 0)
    return {
        "id": e.id,
        "tenant_id": e.tenant_id,
        "category": e.category,
        "content": e.content,
        "source_type": e.source_type,
        "source_id": e.source_id,
        "status": e.status,
        "conflict_with": e.conflict_with,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
        "last_retrieved_at": e.last_retrieved_at.isoformat() if e.last_retrieved_at else None,
        "last_reviewed_at": e.last_reviewed_at.isoformat() if e.last_reviewed_at else None,
        "activity_age_days": activity_age_days,
    }


def _push_to_dict(p: Any) -> dict:
    return {
        "id": p.id,
        "tenant_id": p.tenant_id,
        "recipient_line_id": p.recipient_line_id,
        "message_type": p.message_type,
        "pushed_at": p.pushed_at.isoformat() if p.pushed_at else None,
    }


def _audit_to_dict(a: Any) -> dict:
    return {
        "id": a.id,
        "tenant_id": a.tenant_id,
        "agentos_run_id": a.agentos_run_id,
        "agentos_task_id": a.agentos_task_id,
        "workflow_type": a.workflow_type,
        "event_type": a.event_type,
        "actor_id": a.actor_id,
        "source": a.source,
        "payload": _safe_json(a.payload),
        "created_at": a.created_at.isoformat() if a.created_at else None,
    }


def _connector_to_dict(c: Any) -> dict:
    try:
        creds = json.loads(c.credentials_encrypted)
        has_token = bool(creds.get("access_token") or creds.get("token"))
    except (json.JSONDecodeError, TypeError, AttributeError):
        creds = {}
        has_token = bool(c.credentials_encrypted)
    refresh_info = connector_refresh_state(c)
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "platform": c.platform,
        "account_label": c.account_label,
        "is_active": c.is_active,
        "has_token": has_token,
        "has_refresh_token": refresh_info["has_refresh_token"],
        "can_refresh": refresh_info["can_refresh"],
        "expires_at": refresh_info["expires_at"],
        "refresh_status": refresh_info["refresh_status"],
        "refresh_error": refresh_info["refresh_error"],
        "refresh_failed_at": refresh_info["refresh_failed_at"],
        "last_refreshed_at": c.last_refreshed_at.isoformat() if c.last_refreshed_at else None,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _safe_json(val: str | None) -> Any:
    if not val:
        return {}
    try:
        return json.loads(val)
    except (json.JSONDecodeError, TypeError):
        return {"raw": val}


def _merchant_page_dir(request: Request) -> pathlib.Path:
    return getattr(request.app.state, "merchant_page_dir", _STATIC_DIR / "merchant_pages")


def _merchant_page_base_url(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None)
    return (getattr(settings, "KACHU_BASE_URL", "") or "").strip().rstrip("/")


def _normalize_dashboard_datetime(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _normalize_timezone(value: str | None, *, default: str = "Asia/Taipei", strict: bool = False) -> str:
    timezone_name = (value or "").strip() or default
    try:
        ZoneInfo(timezone_name)
    except Exception:
        if strict:
            raise HTTPException(status_code=400, detail="Invalid timezone")
        return default
    return timezone_name


def _normalize_frequency(value: str) -> str:
    normalized = (value or "").strip().lower()
    return normalized if normalized in {"daily", "weekly", "off"} else "weekly"


def _normalize_weekday(value: str, default: str) -> str:
    normalized = (value or default).strip().lower()[:3]
    return normalized if normalized in {"mon", "tue", "wed", "thu", "fri", "sat", "sun"} else default


def _normalize_hour(value: int, default: int) -> int:
    try:
        hour = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(hour, 0), 23)


def _normalize_day(value: int, default: int) -> int:
    try:
        day = int(value)
    except (TypeError, ValueError):
        return default
    return min(max(day, 1), 28)


def _normalize_plan(value: str | None) -> str:
    normalized = (value or "trial").strip().lower() or "trial"
    return normalized if normalized in {"trial", "starter", "growth", "pro"} else "trial"


def _normalize_optional_merchant_slug(value: str | None) -> str:
    normalized = (value or "").strip()
    if not normalized:
        return ""
    return normalize_merchant_slug(normalized)


def _resolve_merchant_slug(repo: KachuRepository, tenant_id: str, merchant_slug: str | None) -> str:
    normalized = (merchant_slug or "").strip()
    if normalized:
        return normalize_merchant_slug(normalized)

    tenant = repo.get_or_create_tenant(tenant_id)
    configured_slug = (getattr(tenant, "merchant_slug", "") or "").strip()
    if configured_slug:
        return normalize_merchant_slug(configured_slug)
    raise HTTPException(status_code=400, detail="merchant_slug is required")


def _tenant_settings_to_dict(repo: KachuRepository, tenant: Any, flags: Any) -> dict[str, Any]:
    capabilities = {}
    for capability in ("ga4", "meta", "cross_channel", "crm"):
        decision = evaluate_tenant_capability(repo, tenant.id, capability)
        capabilities[capability] = {
            "allowed": decision.allowed,
            "reason": decision.reason,
            "plan": decision.plan,
            "plan_status": decision.plan_status,
        }
    plan_status = evaluate_tenant_capability(repo, tenant.id).plan_status
    return {
        "tenant_id": tenant.id,
        "tenant_name": tenant.name,
        "plan": tenant.plan,
        "plan_expires_at": tenant.plan_expires_at.isoformat() if tenant.plan_expires_at else None,
        "merchant_slug": getattr(tenant, "merchant_slug", "") or "",
        "plan_status": plan_status,
        "is_active": tenant.is_active,
        "ga4_enabled": bool(getattr(flags, "ga4_enabled", True)),
        "meta_enabled": bool(getattr(flags, "meta_enabled", True)),
        "cross_channel_enabled": bool(getattr(flags, "cross_channel_enabled", True)),
        "crm_enabled": bool(getattr(flags, "crm_enabled", False)),
        "capabilities": capabilities,
        "updated_at": getattr(flags, "updated_at", None).isoformat() if getattr(flags, "updated_at", None) else None,
    }


def _automation_settings_to_dict(row: Any, tenant: Any) -> dict[str, Any]:
    return {
        "tenant_id": row.tenant_id,
        "timezone": _normalize_timezone(getattr(tenant, "timezone", "Asia/Taipei") or "Asia/Taipei"),
        "ga_report_enabled": row.ga_report_enabled,
        "ga_report_frequency": row.ga_report_frequency,
        "ga_report_weekday": row.ga_report_weekday,
        "ga_report_hour": row.ga_report_hour,
        "google_post_enabled": row.google_post_enabled,
        "google_post_frequency": row.google_post_frequency,
        "google_post_weekday": row.google_post_weekday,
        "google_post_hour": row.google_post_hour,
        "meta_post_enabled": row.meta_post_enabled,
        "meta_post_frequency": row.meta_post_frequency,
        "meta_post_weekday": row.meta_post_weekday,
        "meta_post_hour": row.meta_post_hour,
        "proactive_enabled": row.proactive_enabled,
        "proactive_hour": row.proactive_hour,
        "content_calendar_enabled": row.content_calendar_enabled,
        "content_calendar_day": row.content_calendar_day,
        "content_calendar_hour": row.content_calendar_hour,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


# ── API: Stats ────────────────────────────────────────────────────────────────


@dashboard_router.get("/api/stats")
def api_stats(request: Request, tenant_id: str | None = None) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    return repo.get_dashboard_stats(tid)


# ── API: Workflow Runs ────────────────────────────────────────────────────────


@dashboard_router.get("/api/runs")
def api_runs(
    request: Request,
    tenant_id: str | None = None,
    workflow_type: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    runs = repo.list_workflow_runs(
        tenant_id=tid,
        workflow_type=workflow_type,
        status=status,
        limit=limit,
    )
    return {"runs": [_run_to_dict(r) for r in runs], "total": len(runs)}


# ── API: Approvals ────────────────────────────────────────────────────────────


@dashboard_router.get("/api/approvals")
def api_approvals(
    request: Request,
    tenant_id: str | None = None,
    status: str | None = None,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    approvals = repo.list_pending_approvals(tenant_id=tid, status=status)
    return {"approvals": [_approval_to_dict(a) for a in approvals]}


# ── API: Knowledge ────────────────────────────────────────────────────────────


class KnowledgeCreateRequest(BaseModel):
    tenant_id: str | None = None
    category: str
    content: str
    source_type: str = "manual"


class KnowledgeUpdateRequest(BaseModel):
    content: str
    category: str | None = None


class KnowledgeLifecycleRefreshRequest(BaseModel):
    tenant_id: str | None = None
    stale_after_days: int = 60


class KnowledgeStatusUpdateRequest(BaseModel):
    tenant_id: str | None = None
    status: str
    conflict_with: str | None = None


class AutomationSettingsUpdateRequest(BaseModel):
    tenant_id: str | None = None
    timezone: str = "Asia/Taipei"
    ga_report_enabled: bool = True
    ga_report_frequency: str = "weekly"
    ga_report_weekday: str = "mon"
    ga_report_hour: int = 8
    google_post_enabled: bool = True
    google_post_frequency: str = "weekly"
    google_post_weekday: str = "thu"
    google_post_hour: int = 10
    meta_post_enabled: bool = False
    meta_post_frequency: str = "weekly"
    meta_post_weekday: str = "fri"
    meta_post_hour: int = 11
    proactive_enabled: bool = True
    proactive_hour: int = 7
    content_calendar_enabled: bool = True
    content_calendar_day: int = 1
    content_calendar_hour: int = 9


class ConnectorDisconnectRequest(BaseModel):
    tenant_id: str | None = None
    platform: str


class ConnectorRefreshRequest(BaseModel):
    tenant_id: str | None = None
    platform: str


class TenantDeactivateRequest(BaseModel):
    tenant_id: str | None = None


class TenantDeleteRequest(BaseModel):
    tenant_id: str | None = None
    confirm_tenant_id: str


class TenantSettingsUpdateRequest(BaseModel):
    tenant_id: str | None = None
    plan: str = "trial"
    plan_expires_at: datetime | None = None
    merchant_slug: str | None = None
    ga4_enabled: bool = True
    meta_enabled: bool = True
    cross_channel_enabled: bool = True
    crm_enabled: bool = False


class MerchantPageUpdateRequest(BaseModel):
    tenant_id: str | None = None
    merchant_slug: str | None = None
    payload: dict[str, Any]


@dashboard_router.get("/api/knowledge")
def api_knowledge(
    request: Request,
    tenant_id: str | None = None,
    category: str | None = None,
    status: str | None = None,
    stale_after_days: int = 60,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    entries = repo.get_knowledge_entries(tid, category=category, status=status)
    return {
        "entries": [_knowledge_to_dict(e) for e in entries],
        "lifecycle": repo.get_knowledge_lifecycle_summary(tid, stale_after_days=stale_after_days),
    }


@dashboard_router.post("/api/knowledge/lifecycle/refresh")
def api_knowledge_refresh_lifecycle(
    body: KnowledgeLifecycleRefreshRequest,
    request: Request,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, body.tenant_id)
    return repo.refresh_knowledge_lifecycle(
        tid,
        stale_after_days=body.stale_after_days,
    )


@dashboard_router.post("/api/knowledge", status_code=201)
def api_knowledge_create(body: KnowledgeCreateRequest, request: Request) -> dict:
    repo = _repo(request)
    tenant_id = (body.tenant_id or "").strip() or _tid(request, None)
    category = body.category.strip()
    content = body.content.strip()

    if not tenant_id:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    if not category:
        raise HTTPException(status_code=400, detail="category is required")
    if not content:
        raise HTTPException(status_code=400, detail="content must not be empty")

    entry = repo.save_knowledge_entry(
        tenant_id=tenant_id,
        category=category,
        content=content,
        source_type=body.source_type,
    )
    return _knowledge_to_dict(entry)


@dashboard_router.put("/api/knowledge/{entry_id}")
def api_knowledge_update(
    entry_id: str, body: KnowledgeUpdateRequest, request: Request
) -> dict:
    repo = _repo(request)
    entry = repo.update_knowledge_entry_content(
        entry_id=entry_id,
        content=body.content,
        category=body.category,
    )
    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")
    return _knowledge_to_dict(entry)


@dashboard_router.post("/api/knowledge/{entry_id}/status")
def api_knowledge_update_status(
    entry_id: str,
    body: KnowledgeStatusUpdateRequest,
    request: Request,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, body.tenant_id)
    existing_entry = repo.get_knowledge_entry(entry_id)
    if existing_entry is None or existing_entry.tenant_id != tid:
        raise HTTPException(status_code=404, detail="Entry not found")

    try:
        entry = repo.update_knowledge_entry_status(
            entry_id,
            status=body.status,
            conflict_with=body.conflict_with,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if entry is None:
        raise HTTPException(status_code=404, detail="Entry not found")

    return {
        "entry": _knowledge_to_dict(entry),
        "lifecycle": repo.get_knowledge_lifecycle_summary(tid),
    }


@dashboard_router.delete("/api/knowledge/{entry_id}", status_code=204)
def api_knowledge_delete(entry_id: str, request: Request) -> Response:
    repo = _repo(request)
    if not repo.delete_knowledge_entry(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return Response(status_code=204)


@dashboard_router.get("/api/merchant-page")
def api_merchant_page(
    request: Request,
    tenant_id: str | None = None,
    merchant_slug: str | None = None,
) -> dict[str, Any]:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    normalized_slug = _resolve_merchant_slug(repo, tid, merchant_slug)
    try:
        payload = load_merchant_page_payload(_merchant_page_dir(request), normalized_slug)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Merchant page not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "merchant_slug": normalized_slug,
        "payload": payload,
        "public_url": payload.get("canonical_url") or f"/merchants/{normalized_slug}",
    }


@dashboard_router.get("/api/merchant-page/template")
def api_merchant_page_template(
    request: Request,
    tenant_id: str | None = None,
    merchant_slug: str | None = None,
) -> dict[str, Any]:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    normalized_slug = _resolve_merchant_slug(repo, tid, merchant_slug)
    tenant = repo.get_or_create_tenant(tid)
    payload = build_merchant_page_template(
        normalized_slug,
        base_url=_merchant_page_base_url(request),
        tenant_name=getattr(tenant, "name", ""),
        industry_type=getattr(tenant, "industry_type", ""),
        address=getattr(tenant, "address", ""),
    )
    return {
        "merchant_slug": normalized_slug,
        "payload": payload,
        "public_url": payload.get("canonical_url") or f"/merchants/{normalized_slug}",
    }


@dashboard_router.put("/api/merchant-page")
def api_update_merchant_page(body: MerchantPageUpdateRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    try:
        normalized_slug = _resolve_merchant_slug(repo, tid, body.merchant_slug)
        payload = save_merchant_page_payload(
            _merchant_page_dir(request),
            normalized_slug,
            body.payload,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    repo.save_audit_event(
        tenant_id=tid,
        event_type="merchant_page_updated",
        source="dashboard",
        actor_id="dashboard_admin",
        payload={"merchant_slug": normalized_slug, "canonical_url": payload.get("canonical_url")},
    )
    return {
        "merchant_slug": normalized_slug,
        "payload": payload,
        "public_url": payload.get("canonical_url") or f"/merchants/{normalized_slug}",
    }


# ── API: Automation Settings ────────────────────────────────────────────────


@dashboard_router.get("/api/automation-settings")
def api_automation_settings(request: Request, tenant_id: str | None = None) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id is required")
    tenant = repo.get_or_create_tenant(tid)
    row = repo.get_or_create_automation_settings(tid)
    return _automation_settings_to_dict(row, tenant)


@dashboard_router.put("/api/automation-settings")
def api_update_automation_settings(body: AutomationSettingsUpdateRequest, request: Request) -> dict:
    repo = _repo(request)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    if not tid:
        raise HTTPException(status_code=400, detail="tenant_id is required")

    tenant = repo.get_or_create_tenant(tid)
    tenant.timezone = _normalize_timezone(body.timezone or tenant.timezone or "Asia/Taipei", strict=True)
    repo.save_tenant(tenant)

    row = repo.update_automation_settings(
        tid,
        ga_report_enabled=body.ga_report_enabled,
        ga_report_frequency=_normalize_frequency(body.ga_report_frequency),
        ga_report_weekday=_normalize_weekday(body.ga_report_weekday, "mon"),
        ga_report_hour=_normalize_hour(body.ga_report_hour, 8),
        google_post_enabled=body.google_post_enabled,
        google_post_frequency=_normalize_frequency(body.google_post_frequency),
        google_post_weekday=_normalize_weekday(body.google_post_weekday, "thu"),
        google_post_hour=_normalize_hour(body.google_post_hour, 10),
        meta_post_enabled=body.meta_post_enabled,
        meta_post_frequency=_normalize_frequency(body.meta_post_frequency),
        meta_post_weekday=_normalize_weekday(body.meta_post_weekday, "fri"),
        meta_post_hour=_normalize_hour(body.meta_post_hour, 11),
        proactive_enabled=body.proactive_enabled,
        proactive_hour=_normalize_hour(body.proactive_hour, 7),
        content_calendar_enabled=body.content_calendar_enabled,
        content_calendar_day=_normalize_day(body.content_calendar_day, 1),
        content_calendar_hour=_normalize_hour(body.content_calendar_hour, 9),
    )
    return _automation_settings_to_dict(row, tenant)


# ── API: Connectors ───────────────────────────────────────────────────────────


def _check_connector_env(platform: str, settings: Any) -> tuple[bool, str]:
    """
    Return (is_connected, hint) based on env/settings.
    Uses strict checks — a default placeholder path does NOT count as connected.
    """
    import os

    if settings is None:
        return False, "未設定"

    if platform == "line":
        token = (getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or "").strip()
        secret = (getattr(settings, "LINE_CHANNEL_SECRET", "") or "").strip()
        if token and secret:
            return True, ""
        missing = []
        if not token:
            missing.append("LINE_CHANNEL_ACCESS_TOKEN")
        if not secret:
            missing.append("LINE_CHANNEL_SECRET")
        return False, f"請設定 {' 及 '.join(missing)}"

    if platform == "google_business":
        # 1. Prefer OAuth path: GOOGLE_BUSINESS_ACCOUNT_ID + GOOGLE_OAUTH_CLIENT_ID
        account_id = (getattr(settings, "GOOGLE_BUSINESS_ACCOUNT_ID", "") or "").strip()
        oauth_client = (getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "") or "").strip()
        if account_id and oauth_client:
            return True, ""
        # 2. Service-account file path must be non-default AND the file must exist
        sa_path = (getattr(settings, "GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip()
        default_path = "credentials/google-service-account.json"
        if sa_path and sa_path != default_path and os.path.isfile(sa_path):
            if account_id:
                return True, ""
        return False, "請完成 Google OAuth 授權或設定 Service Account"

    if platform == "ga4":
        property_id = (getattr(settings, "GA4_PROPERTY_ID", "") or "").strip()
        oauth_client = (getattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "") or "").strip()
        feature = getattr(settings, "FEATURE_GA4", False)
        if property_id and (oauth_client or feature):
            return True, ""
        missing = []
        if not property_id:
            missing.append("GA4_PROPERTY_ID")
        if not oauth_client:
            missing.append("GOOGLE_OAUTH_CLIENT_ID")
        return False, f"請設定 {' 及 '.join(missing)}"

    if platform == "meta":
        app_id = (getattr(settings, "META_APP_ID", "") or "").strip()
        app_secret = (getattr(settings, "META_APP_SECRET", "") or "").strip()
        feature = getattr(settings, "FEATURE_META", False)
        if not feature:
            return False, "尚未整合（Phase 3）"
        if app_id and app_secret:
            return True, ""
        return False, "請設定 META_APP_ID 及 META_APP_SECRET"

    return False, "未知平台"


@dashboard_router.get("/api/connectors")
def api_connectors(request: Request, tenant_id: str | None = None) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    settings = getattr(request.app.state, "settings", None)

    platforms = ["google_business", "ga4", "meta", "line"]
    connectors = []
    for platform in platforms:
        # Prefer DB record if available
        if tid:
            record = repo.get_connector_account(tid, platform)
            if record:
                connectors.append(_connector_to_dict(record))
                continue
        # Fall back to env-based detection
        connected, hint = _check_connector_env(platform, settings)
        connectors.append({
            "id": None,
            "tenant_id": tid,
            "platform": platform,
            "account_label": "",
            "is_active": connected,
            "has_token": connected,
            "hint": hint,
            "last_refreshed_at": None,
            "created_at": None,
        })
    return {"connectors": connectors}


@dashboard_router.post("/api/connectors/disconnect")
def api_disconnect_connector(body: ConnectorDisconnectRequest, request: Request) -> dict:
    repo = _repo(request)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    platform = (body.platform or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="platform is required")

    disconnected = repo.disconnect_connector_account(tenant_id=tid, platform=platform)
    if disconnected is None:
        raise HTTPException(status_code=404, detail="Connector not found")

    repo.save_audit_event(
        tenant_id=tid,
        event_type="connector_disconnected",
        source="dashboard",
        actor_id="dashboard_admin",
        payload={"platform": platform},
    )
    return {
        "status": "disconnected",
        "connector": _connector_to_dict(disconnected),
    }


@dashboard_router.post("/api/connectors/refresh")
def api_refresh_connector(body: ConnectorRefreshRequest, request: Request) -> dict:
    repo = _repo(request)
    settings = getattr(request.app.state, "settings", None)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    platform = (body.platform or "").strip()
    if not platform:
        raise HTTPException(status_code=400, detail="platform is required")
    try:
        refreshed, _, did_refresh = refresh_google_connector_credentials(
            repo,
            settings,
            tenant_id=tid,
            platform=platform,
            force=True,
            source="dashboard",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "status": "refreshed" if did_refresh else "already_fresh",
        "connector": _connector_to_dict(refreshed),
    }


@dashboard_router.get("/api/tenants/settings")
def api_tenant_settings(request: Request, tenant_id: str | None = None) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    tenant = repo.get_or_create_tenant(tid)
    flags = repo.get_or_create_tenant_feature_flags(tid)
    return _tenant_settings_to_dict(repo, tenant, flags)


@dashboard_router.put("/api/tenants/settings")
def api_update_tenant_settings(body: TenantSettingsUpdateRequest, request: Request) -> dict:
    repo = _repo(request)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    normalized_merchant_slug = _normalize_optional_merchant_slug(body.merchant_slug)
    tenant = repo.update_tenant_plan(
        tid,
        plan=_normalize_plan(body.plan),
        plan_expires_at=body.plan_expires_at,
        merchant_slug=normalized_merchant_slug,
    )
    flags = repo.update_tenant_feature_flags(
        tid,
        ga4_enabled=body.ga4_enabled,
        meta_enabled=body.meta_enabled,
        cross_channel_enabled=body.cross_channel_enabled,
        crm_enabled=body.crm_enabled,
    )
    repo.save_audit_event(
        tenant_id=tid,
        event_type="tenant_settings_updated",
        source="dashboard",
        actor_id="dashboard_admin",
        payload={
            "plan": tenant.plan,
            "plan_expires_at": tenant.plan_expires_at.isoformat() if tenant.plan_expires_at else None,
            "merchant_slug": tenant.merchant_slug,
            "feature_flags": {
                "ga4_enabled": flags.ga4_enabled,
                "meta_enabled": flags.meta_enabled,
                "cross_channel_enabled": flags.cross_channel_enabled,
                "crm_enabled": flags.crm_enabled,
            },
        },
    )
    return _tenant_settings_to_dict(repo, tenant, flags)


@dashboard_router.get("/api/tenants/health")
def api_tenant_health(request: Request, tenant_id: str | None = None) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    snapshot = repo.get_tenant_health_snapshot(tid)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return snapshot


@dashboard_router.post("/api/tenants/deactivate")
def api_deactivate_tenant(body: TenantDeactivateRequest, request: Request) -> dict:
    repo = _repo(request)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    tenant = repo.deactivate_tenant(tid)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    repo.save_audit_event(
        tenant_id=tid,
        event_type="tenant_deactivated",
        source="dashboard",
        actor_id="dashboard_admin",
        payload={"tenant_id": tid},
    )
    return {
        "status": "deactivated",
        "tenant": {
            "id": tenant.id,
            "is_active": tenant.is_active,
            "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
        },
    }


@dashboard_router.get("/api/tenants/export")
def api_export_tenant(request: Request, tenant_id: str | None = None) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    exported = repo.export_tenant_bundle(tid)
    if exported is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return exported


@dashboard_router.post("/api/tenants/delete")
def api_delete_tenant(body: TenantDeleteRequest, request: Request) -> dict:
    repo = _repo(request)
    tid = (body.tenant_id or "").strip() or _tid(request, None)
    if body.confirm_tenant_id.strip() != tid:
        raise HTTPException(status_code=400, detail="confirm_tenant_id must match tenant_id")

    deleted_counts = repo.delete_tenant_bundle(tid)
    if deleted_counts is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return {
        "status": "deleted",
        "tenant_id": tid,
        "deleted_counts": deleted_counts,
    }


# ── API: Push Log ─────────────────────────────────────────────────────────────


@dashboard_router.get("/api/pushes")
def api_pushes(
    request: Request,
    tenant_id: str | None = None,
    limit: int = 50,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    pushes = repo.list_push_logs(tenant_id=tid, limit=limit)
    today_count = repo.count_pushes_today(tid) if tid else 0
    can_push = repo.can_push(tid) if tid else True
    return {
        "pushes": [_push_to_dict(p) for p in pushes],
        "today_count": today_count,
        "daily_limit": 3,
        "can_push": can_push,
    }


@dashboard_router.get("/api/audit")
def api_audit(
    request: Request,
    tenant_id: str | None = None,
    run_id: str | None = None,
    workflow_type: str | None = None,
    event_type: str | None = None,
    source: str | None = None,
    limit: int = 100,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    events = repo.list_audit_events(
        tenant_id=tid,
        agentos_run_id=run_id,
        workflow_type=workflow_type,
        event_type=event_type,
        source=source,
        limit=limit,
    )
    return {"events": [_audit_to_dict(event) for event in events], "total": len(events)}
