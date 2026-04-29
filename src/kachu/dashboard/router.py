from __future__ import annotations

import json
import pathlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.responses import Response
from pydantic import BaseModel

from ..persistence import KachuRepository

dashboard_router = APIRouter(prefix="/dashboard", tags=["dashboard"])

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
    """If tenant_id not provided in query, fall back to configured boss user ID."""
    if tenant_id:
        return tenant_id
    settings = getattr(request.app.state, "settings", None)
    if settings:
        boss_id = getattr(settings, "LINE_BOSS_USER_ID", None)
        if boss_id:
            return boss_id
    return None


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
    return {
        "id": e.id,
        "tenant_id": e.tenant_id,
        "category": e.category,
        "content": e.content,
        "source_type": e.source_type,
        "source_id": e.source_id,
        "status": e.status,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "updated_at": e.updated_at.isoformat() if e.updated_at else None,
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
        has_token = bool(c.credentials_encrypted)
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "platform": c.platform,
        "account_label": c.account_label,
        "is_active": c.is_active,
        "has_token": has_token,
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


@dashboard_router.get("/api/knowledge")
def api_knowledge(
    request: Request,
    tenant_id: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> dict:
    repo = _repo(request)
    tid = _tid(request, tenant_id)
    if not tid:
        return {"entries": []}
    entries = repo.get_knowledge_entries(tid, category=category)
    if status:
        entries = [e for e in entries if e.status == status]
    return {"entries": [_knowledge_to_dict(e) for e in entries]}


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


@dashboard_router.delete("/api/knowledge/{entry_id}", status_code=204)
def api_knowledge_delete(entry_id: str, request: Request) -> Response:
    repo = _repo(request)
    if not repo.delete_knowledge_entry(entry_id):
        raise HTTPException(status_code=404, detail="Entry not found")
    return Response(status_code=204)


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
