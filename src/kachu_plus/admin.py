"""
Admin API（A-1）：平台管理者操作介面。

所有 endpoint 以 Bearer token 保護（ADMIN_API_TOKEN 環境變數）。
商家導入 SOP 的開通步驟透過這些 API 完成，工程師不需要直接操作 DB。

Endpoints：
    POST  /admin/tenants                  — 建立 tenant + LINE channel config + owner membership
    GET   /admin/tenants                  — 列出所有 active tenant
    GET   /admin/tenants/{id}             — 查看單一 tenant 詳情
    PUT   /admin/tenants/{id}/line-channel — 更新 LINE channel config
    PATCH /admin/tenants/{id}/deactivate  — 停用 tenant
"""
from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlmodel import Session, select

from kachu_plus.config import get_settings
from kachu_plus.crypto import encrypt_field
from kachu_plus.line.webhook import replay_stored_line_webhook_event
from kachu_plus.meta import replay_stored_meta_webhook_event
from kachu_plus.persistence.tables import (
    LineChannelConfigTable,
    TenantMembershipTable,
    TenantTable,
    new_id,
    utcnow,
)

router = APIRouter(prefix="/admin", tags=["admin"])


# ── Auth ──────────────────────────────────────────────────────────────────────


def _require_admin(
    request: Request,
    authorization: str = Header(default=""),
) -> None:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    expected = (getattr(settings, "ADMIN_API_TOKEN", "") or "").strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="Admin API not configured: set ADMIN_API_TOKEN in environment",
        )
    scheme, _, token = authorization.partition(" ")
    provided = token.strip() if scheme.lower() == "bearer" else ""
    if not provided or not secrets.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="Invalid admin token")


# ── Request / Response models ────────────────────────────────────────────────


class TenantProvisionRequest(BaseModel):
    """商家開通所需的完整資訊。"""
    # 商家基本資料
    name: str
    industry_type: str = ""
    address: str = ""
    plan: str = "trial"
    # LINE channel（必填，onboarding 必須有才能收發訊息）
    line_channel_id: str
    line_channel_secret: str
    line_channel_access_token: str
    # 老闆個人 LINE user_id（以 U 開頭）
    owner_line_user_id: str
    owner_display_name: str = ""


class LineChannelUpdateRequest(BaseModel):
    """更新 LINE channel config（可只更新部分欄位）。"""
    channel_id: Optional[str] = None
    channel_secret: Optional[str] = None
    channel_access_token: Optional[str] = None


class ReplayWebhookEventRequest(BaseModel):
    note: str = ""
    
class ReplayWebhookEventsBatchRequest(BaseModel):
    event_ids: list[str]
    note: str = ""
    stop_on_error: bool = False


class ReplayWebhookEventsQueryRequest(BaseModel):
    provider: str = ""
    event_type: str = ""
    external_user_id: str = ""
    external_thread_id: str = ""
    replay_policy: str = "all"
    limit: int = 50
    note: str = ""
    stop_on_error: bool = False
    dry_run: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────


def _engine(request: Request):
    return request.app.state.repository._engine


def _enc_key(request: Request) -> str:
    settings = getattr(request.app.state, "settings", None) or get_settings()
    return (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()


def _tenant_summary(tenant: TenantTable, *, repo, webhook_base: str = "") -> dict[str, Any]:
    config = repo.get_line_channel_config(tenant.id)
    memberships = repo.list_active_memberships(tenant.id)
    onboarding_complete = repo.is_onboarding_complete(tenant.id)
    return {
        "tenant_id": tenant.id,
        "name": tenant.name,
        "industry_type": tenant.industry_type,
        "address": tenant.address,
        "plan": tenant.plan,
        "sleep_threshold": tenant.sleep_threshold,
        "is_active": tenant.is_active,
        "onboarding_complete": onboarding_complete,
        "webhook_url": f"{webhook_base}/webhooks/line/{tenant.id}",
        "has_line_config": config is not None,
        "line_channel_id": config.channel_id if config else "",
        "memberships": [
            {"line_user_id": m.line_user_id, "role": m.role, "display_name": m.display_name}
            for m in memberships
        ],
        "created_at": tenant.created_at.isoformat() if tenant.created_at else None,
        "updated_at": tenant.updated_at.isoformat() if tenant.updated_at else None,
    }


def _decode_json(raw: str) -> Any:
    try:
        return json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return None


def _serialize_webhook_event(event: Any, *, include_payload: bool = False) -> dict[str, Any]:
    result = {
        "id": event.id,
        "tenant_id": event.tenant_id,
        "provider": event.provider,
        "event_type": event.event_type,
        "dedupe_key": event.dedupe_key,
        "external_event_id": event.external_event_id,
        "external_user_id": event.external_user_id,
        "external_thread_id": event.external_thread_id,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "received_at": event.received_at.isoformat() if event.received_at else None,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
    if include_payload:
        result["raw_payload"] = _decode_json(str(getattr(event, "raw_payload_json", "") or "{}"))
    return result
    
async def _replay_webhook_event_with_audit(
    *,
    tenant_id: str,
    event: Any,
    request: Request,
    note: str = "",
) -> dict[str, Any]:
    repo = request.app.state.repository
    provider = str(event.provider or "")
    try:
        if provider == "meta":
            replay = await replay_stored_meta_webhook_event(request=request, event=event)
        elif provider == "line":
            replay = await replay_stored_line_webhook_event(request=request, event=event)
        else:
            raise HTTPException(status_code=400, detail="Replay currently supports meta and line webhook events only")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    
    audit = repo.save_conversation(
        tenant_id=tenant_id,
        actor_role="system",
        channel_type="admin",
        conversation_kind="event_replay",
        content_text=f"Replayed {event.provider} webhook event {event.id}",
        metadata={
            "webhook_event_id": event.id,
            "provider": event.provider,
            "event_type": event.event_type,
            "note": note,
            "replay": replay,
        },
    )
    return {
        "event": _serialize_webhook_event(event),
        "replay": replay,
        "audit_conversation_id": audit.id,
    }


def _event_matches_replay_policy(*, repo: Any, event: Any, replay_policy: str) -> bool:
    policy = str(replay_policy or "all").strip() or "all"
    if policy == "all":
        return True

    provider = str(getattr(event, "provider", "") or "")
    event_type = str(getattr(event, "event_type", "") or "")
    external_event_id = str(getattr(event, "external_event_id", "") or "")
    if provider != "meta" or event_type not in {"comment", "message"} or not external_event_id:
        return False

    engagement = repo.get_external_engagement_by_message_id(external_event_id)
    if policy == "missing_engagement":
        return engagement is None
    if policy == "missing_pending_approval":
        if engagement is None:
            return False
        run_id = str(getattr(engagement, "related_run_id", "") or f"meta-engagement:{external_event_id}")
        return repo.get_pending_approval_by_run_id(run_id) is None

    raise HTTPException(status_code=400, detail=f"unsupported replay_policy: {policy}")


# ── Endpoints ─────────────────────────────────────────────────────────────────


@router.post("/tenants", status_code=201, dependencies=[Depends(_require_admin)])
def provision_tenant(body: TenantProvisionRequest, request: Request) -> dict[str, Any]:
    """
    建立商家 tenant + LINE channel config + owner membership（三步驟一次完成）。
    完成後回傳 tenant_id 與 webhook_url，工程師直接貼入 LINE Developers Console。
    """
    repo = request.app.state.repository
    enc_key = _enc_key(request)
    settings = getattr(request.app.state, "settings", None) or get_settings()

    # 基本驗證
    if not body.name.strip():
        raise HTTPException(status_code=400, detail="name is required")
    if not body.line_channel_id.strip():
        raise HTTPException(status_code=400, detail="line_channel_id is required")
    if not body.line_channel_secret.strip():
        raise HTTPException(status_code=400, detail="line_channel_secret is required")
    if not body.line_channel_access_token.strip():
        raise HTTPException(status_code=400, detail="line_channel_access_token is required")
    if not body.owner_line_user_id.strip():
        raise HTTPException(status_code=400, detail="owner_line_user_id is required")

    tenant_id = new_id()
    now = utcnow()

    with Session(_engine(request)) as session:
        tenant = TenantTable(
            id=tenant_id,
            name=body.name.strip(),
            industry_type=body.industry_type.strip(),
            address=body.address.strip(),
            plan=body.plan,
            created_at=now,
            updated_at=now,
        )
        session.add(tenant)
        session.flush()  # 確保 tenant 先寫入，FK constraint 才不會失敗

        # 加密敏感欄位後存入（若未設 FIELD_ENCRYPTION_KEY 則明文）
        line_config = LineChannelConfigTable(
            tenant_id=tenant_id,
            channel_id=body.line_channel_id.strip(),
            channel_secret=encrypt_field(body.line_channel_secret.strip(), enc_key),
            channel_access_token=encrypt_field(body.line_channel_access_token.strip(), enc_key),
            is_active=True,
            created_at=now,
            updated_at=now,
        )
        session.add(line_config)

        membership = TenantMembershipTable(
            tenant_id=tenant_id,
            line_user_id=body.owner_line_user_id.strip(),
            role="owner",
            display_name=(body.owner_display_name or body.name).strip(),
            created_at=now,
            updated_at=now,
        )
        session.add(membership)
        session.commit()

    base_url = (getattr(settings, "KACHU_BASE_URL", "") or "").rstrip("/")
    webhook_url = f"{base_url}/webhooks/line/{tenant_id}"

    return {
        "tenant_id": tenant_id,
        "name": body.name,
        "plan": body.plan,
        "webhook_url": webhook_url,
        "next_steps": [
            f"1. 前往 LINE Developers Console，將 Webhook URL 設為：{webhook_url}",
            "2. 確認 Use webhook 已勾選，點擊 Verify",
            "3. 關閉 LINE OA 的自動回覆訊息",
            "4. 通知商家老闆加入 LINE OA 好友，並傳第一則訊息",
        ],
    }


@router.get("/tenants", dependencies=[Depends(_require_admin)])
def list_tenants(request: Request) -> dict[str, Any]:
    """列出所有 active tenant 及其開通狀態。"""
    repo = request.app.state.repository
    settings = getattr(request.app.state, "settings", None) or get_settings()
    base_url = (getattr(settings, "KACHU_BASE_URL", "") or "").rstrip("/")
    tenants = repo.list_active_tenants()
    result = [_tenant_summary(t, repo=repo, webhook_base=base_url) for t in tenants]
    return {"tenants": result, "total": len(result)}


@router.get("/tenants/{tenant_id}", dependencies=[Depends(_require_admin)])
def get_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """取得單一 tenant 詳情，含 membership 與開通狀態。"""
    repo = request.app.state.repository
    settings = getattr(request.app.state, "settings", None) or get_settings()
    base_url = (getattr(settings, "KACHU_BASE_URL", "") or "").rstrip("/")
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return _tenant_summary(tenant, repo=repo, webhook_base=base_url)


@router.put("/tenants/{tenant_id}/line-channel", dependencies=[Depends(_require_admin)])
def update_line_channel(
    tenant_id: str,
    body: LineChannelUpdateRequest,
    request: Request,
) -> dict[str, Any]:
    """
    更新 LINE channel config（可只更新其中幾個欄位）。
    常見用途：token 過期後重新設定 channel_access_token。
    """
    repo = request.app.state.repository
    enc_key = _enc_key(request)

    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    now = utcnow()
    with Session(_engine(request)) as session:
        stmt = select(LineChannelConfigTable).where(
            LineChannelConfigTable.tenant_id == tenant_id
        )
        config = session.exec(stmt).first()

        if config is None:
            # 首次建立 config（tenant 是先建的情況）
            if not body.channel_id or not body.channel_secret or not body.channel_access_token:
                raise HTTPException(
                    status_code=400,
                    detail="New config requires channel_id, channel_secret, and channel_access_token",
                )
            config = LineChannelConfigTable(
                tenant_id=tenant_id,
                channel_id=body.channel_id.strip(),
                channel_secret=encrypt_field(body.channel_secret.strip(), enc_key),
                channel_access_token=encrypt_field(body.channel_access_token.strip(), enc_key),
                is_active=True,
                updated_at=now,
            )
        else:
            if body.channel_id is not None:
                config.channel_id = body.channel_id.strip()
            if body.channel_secret is not None:
                config.channel_secret = encrypt_field(body.channel_secret.strip(), enc_key)
            if body.channel_access_token is not None:
                config.channel_access_token = encrypt_field(body.channel_access_token.strip(), enc_key)
            config.updated_at = now

        session.add(config)
        session.commit()
        session.refresh(config)

    return {
        "tenant_id": tenant_id,
        "channel_id": config.channel_id,
        "is_active": config.is_active,
        "updated_at": config.updated_at.isoformat() if config.updated_at else None,
        "message": "LINE channel config updated",
    }


@router.patch("/tenants/{tenant_id}/deactivate", dependencies=[Depends(_require_admin)])
def deactivate_tenant(tenant_id: str, request: Request) -> dict[str, Any]:
    """停用 tenant（is_active → False），不刪除資料。"""
    repo = request.app.state.repository
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if not tenant.is_active:
        return {"tenant_id": tenant_id, "is_active": False, "message": "Already inactive"}
    tenant.is_active = False
    repo.save_tenant(tenant)
    return {"tenant_id": tenant_id, "is_active": False, "message": "Tenant deactivated"}


@router.get("/tenants/{tenant_id}/events", dependencies=[Depends(_require_admin)])
def list_tenant_events(
    tenant_id: str,
    request: Request,
    provider: str = Query(default=""),
    event_type: str = Query(default=""),
    external_user_id: str = Query(default=""),
    external_thread_id: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    events = repo.list_webhook_events(
        tenant_id,
        provider=provider or None,
        event_type=event_type or None,
        external_user_id=external_user_id or None,
        external_thread_id=external_thread_id or None,
        limit=limit,
    )
    return {"events": [_serialize_webhook_event(event) for event in events], "total": len(events)}


@router.get("/tenants/{tenant_id}/events/{event_id}", dependencies=[Depends(_require_admin)])
def get_tenant_event(tenant_id: str, event_id: str, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    event = repo.get_webhook_event(event_id)
    if event is None or event.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Webhook event not found")
    return {"event": _serialize_webhook_event(event, include_payload=True)}


@router.post("/tenants/{tenant_id}/events/{event_id}/replay", dependencies=[Depends(_require_admin)])
async def replay_tenant_event(
    tenant_id: str,
    event_id: str,
    request: Request,
    body: ReplayWebhookEventRequest | None = None,
) -> dict[str, Any]:
    repo = request.app.state.repository
    event = repo.get_webhook_event(event_id)
    if event is None or event.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="Webhook event not found")
    return await _replay_webhook_event_with_audit(
        tenant_id=tenant_id,
        event=event,
        request=request,
        note=str((body.note if body else "") or "").strip(),
    )


@router.post("/tenants/{tenant_id}/events/replay-batch", dependencies=[Depends(_require_admin)])
async def replay_tenant_events_batch(
    tenant_id: str,
    request: Request,
    body: ReplayWebhookEventsBatchRequest,
) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    event_ids = [str(event_id or "").strip() for event_id in body.event_ids if str(event_id or "").strip()]
    if not event_ids:
        raise HTTPException(status_code=400, detail="event_ids is required")

    results: list[dict[str, Any]] = []
    success_count = 0
    error_count = 0
    note = str(body.note or "").strip()
    for event_id in event_ids:
        event = repo.get_webhook_event(event_id)
        if event is None or event.tenant_id != tenant_id:
            result = {"event_id": event_id, "status": "error", "detail": "Webhook event not found"}
            results.append(result)
            error_count += 1
            if body.stop_on_error:
                break
            continue
        try:
            replayed = await _replay_webhook_event_with_audit(
                tenant_id=tenant_id,
                event=event,
                request=request,
                note=note,
            )
        except HTTPException as exc:
            result = {"event_id": event_id, "status": "error", "detail": str(exc.detail)}
            results.append(result)
            error_count += 1
            if body.stop_on_error:
                break
            continue
        results.append({"event_id": event_id, "status": "ok", **replayed})
        success_count += 1

    return {
        "results": results,
        "requested_count": len(event_ids),
        "success_count": success_count,
        "error_count": error_count,
    }


@router.post("/tenants/{tenant_id}/events/replay-query", dependencies=[Depends(_require_admin)])
async def replay_tenant_events_query(
    tenant_id: str,
    request: Request,
    body: ReplayWebhookEventsQueryRequest,
) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    limit = max(min(int(body.limit or 50), 200), 1)
    events = repo.list_webhook_events(
        tenant_id,
        provider=str(body.provider or "").strip() or None,
        event_type=str(body.event_type or "").strip() or None,
        external_user_id=str(body.external_user_id or "").strip() or None,
        external_thread_id=str(body.external_thread_id or "").strip() or None,
        limit=limit,
    )
    events = [
        event for event in events if _event_matches_replay_policy(repo=repo, event=event, replay_policy=body.replay_policy)
    ]

    if body.dry_run:
        return {
            "mode": "dry_run",
            "replay_policy": str(body.replay_policy or "all").strip() or "all",
            "requested_count": len(events),
            "selected_events": [_serialize_webhook_event(event) for event in events],
        }

    results: list[dict[str, Any]] = []
    success_count = 0
    error_count = 0
    note = str(body.note or "").strip()
    for event in events:
        try:
            replayed = await _replay_webhook_event_with_audit(
                tenant_id=tenant_id,
                event=event,
                request=request,
                note=note,
            )
        except HTTPException as exc:
            results.append({"event_id": event.id, "status": "error", "detail": str(exc.detail)})
            error_count += 1
            if body.stop_on_error:
                break
            continue
        results.append({"event_id": event.id, "status": "ok", **replayed})
        success_count += 1

    return {
        "mode": "executed",
        "replay_policy": str(body.replay_policy or "all").strip() or "all",
        "requested_count": len(events),
        "success_count": success_count,
        "error_count": error_count,
        "results": results,
    }
