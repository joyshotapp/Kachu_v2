from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from kachu_plus.line.push import push_line_messages, text_message
from kachu_plus.models import ExecutionTaskResult
from kachu_plus.proactive import NUDGE_NEGATIVE_REVIEW, NUDGE_NO_POST, NUDGE_SLEEPING_CUSTOMERS

router = APIRouter(tags=["suggestions"])

STATUS_PENDING = "pending"
STATUS_ACCEPTED = "accepted"
STATUS_SENDING = "sending"
STATUS_SENT = "sent"
STATUS_DISMISSED = "dismissed"
STATUS_EXPIRED = "expired"
STATUS_REPORTED = "reported"

EDITABLE_DRAFT_STATUSES = {STATUS_PENDING, STATUS_ACCEPTED}
ACTIONABLE_DECISION_STATUSES = {STATUS_PENDING, STATUS_ACCEPTED}


class SuggestionDecisionBody(BaseModel):
    action: str
    actor_line_id: str = "owner"
    draft_message: str | None = None
    execute_now: bool = False


class SuggestionSendBody(BaseModel):
    actor_line_id: str = "owner"
    draft_message: str | None = None


class SuggestionReportBody(BaseModel):
    actor_line_id: str = "owner"
    metrics: dict[str, Any] = Field(default_factory=dict)
    note: str = ""


@router.post("/tenants/{tenant_id}/suggestions/scan")
async def scan_suggestions(tenant_id: str, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    engine = request.app.state.proactive_suggestion_engine
    result = engine.run_once_for_tenant(tenant_id)
    return {"suggestion": result}


@router.get("/tenants/{tenant_id}/suggestions")
def list_suggestions(tenant_id: str, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    suggestions = repo.list_pending_suggestions(tenant_id)
    return {
        "suggestions": [_serialize_suggestion(suggestion) for suggestion in suggestions]
    }

@router.get("/tenants/{tenant_id}/suggestions/metrics")
def suggestion_metrics(tenant_id: str, request: Request, window_days: int = 7) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")

    days = max(int(window_days), 1)
    now = _utcnow()
    start_at = now - timedelta(days=days)
    suggestions = repo.list_suggestions(tenant_id, limit=500)
    windowed = [suggestion for suggestion in suggestions if (_utc_value(suggestion.created_at) or now) >= start_at]

    actionable = [item for item in windowed if item.status in {STATUS_ACCEPTED, STATUS_SENDING, STATUS_DISMISSED, STATUS_SENT, STATUS_REPORTED, STATUS_EXPIRED}]
    accepted = [item for item in actionable if item.status in {STATUS_ACCEPTED, STATUS_SENDING, STATUS_SENT, STATUS_REPORTED}]
    reported = [item for item in windowed if item.status == STATUS_REPORTED]
    response_rates = [
        float(metrics["response_rate"])
        for metrics in (_result_snapshot(item).get("metrics", {}) for item in reported)
        if isinstance(metrics.get("response_rate"), (int, float))
    ]
    booked_counts = [
        int(metrics["booked_count"])
        for metrics in (_result_snapshot(item).get("metrics", {}) for item in reported)
        if isinstance(metrics.get("booked_count"), (int, float))
    ]

    by_type: dict[str, dict[str, Any]] = {}
    for item in windowed:
        bucket = by_type.setdefault(
            item.suggestion_type,
            {
                "created_count": 0,
                "accepted_count": 0,
                "reported_count": 0,
                "average_response_rate": None,
            },
        )
        bucket["created_count"] += 1
        if item.status in {STATUS_ACCEPTED, STATUS_SENDING, STATUS_SENT, STATUS_REPORTED}:
            bucket["accepted_count"] += 1
        if item.status == STATUS_REPORTED:
            bucket["reported_count"] += 1
            metrics = _result_snapshot(item).get("metrics", {})
            if isinstance(metrics.get("response_rate"), (int, float)):
                bucket.setdefault("_response_rates", []).append(float(metrics["response_rate"]))
    for bucket in by_type.values():
        values = bucket.pop("_response_rates", [])
        if values:
            bucket["average_response_rate"] = round(sum(values) / len(values), 4)

    return {
        "window_days": days,
        "start_at": start_at.isoformat(),
        "end_at": now.isoformat(),
        "created_count": len(windowed),
        "actionable_count": len(actionable),
        "accepted_count": len(accepted),
        "reported_count": len(reported),
        "acceptance_rate": round(len(accepted) / len(actionable), 4) if actionable else 0.0,
        "average_response_rate": round(sum(response_rates) / len(response_rates), 4) if response_rates else None,
        "total_booked_count": sum(booked_counts),
        "by_type": by_type,
    }
from datetime import timedelta


@router.post("/tenants/{tenant_id}/suggestions/{suggestion_id}/decision")
async def decide_suggestion(
    tenant_id: str,
    suggestion_id: str,
    body: SuggestionDecisionBody,
    request: Request,
) -> dict[str, Any]:
    normalized_action = body.action.strip().lower()
    if normalized_action not in {"accept", "dismiss"}:
        raise HTTPException(status_code=400, detail="action must be accept or dismiss")
    suggestion = await handle_suggestion_action(
        request=request,
        tenant_id=tenant_id,
        suggestion_id=suggestion_id,
        action=normalized_action,
        actor_line_id=body.actor_line_id,
        draft_message=body.draft_message,
        execute_now=body.execute_now,
    )
    return {"suggestion": suggestion}


@router.post("/tenants/{tenant_id}/suggestions/{suggestion_id}/send")
async def send_suggestion(
    tenant_id: str,
    suggestion_id: str,
    body: SuggestionSendBody,
    request: Request,
) -> dict[str, Any]:
    suggestion = await send_suggestion_execution(
        request=request,
        tenant_id=tenant_id,
        suggestion_id=suggestion_id,
        actor_line_id=body.actor_line_id,
        draft_message=body.draft_message,
    )
    return {"suggestion": suggestion}


@router.post("/tenants/{tenant_id}/suggestions/{suggestion_id}/report")
async def report_suggestion(
    tenant_id: str,
    suggestion_id: str,
    body: SuggestionReportBody,
    request: Request,
) -> dict[str, Any]:
    repo = request.app.state.repository
    suggestion = _require_suggestion(repo, tenant_id, suggestion_id)
    if suggestion.status not in {STATUS_SENT, STATUS_REPORTED}:
        raise HTTPException(status_code=409, detail="suggestion must be sent before reporting")
    snapshot = _result_snapshot(suggestion)
    snapshot["reported_at"] = _utcnow().isoformat()
    snapshot["reported_by"] = body.actor_line_id
    snapshot["metrics"] = body.metrics
    if body.note:
        snapshot["note"] = body.note
    updated = repo.update_suggestion_status(
        suggestion_id=suggestion_id,
        status=STATUS_REPORTED,
        result_snapshot=snapshot,
        allowed_current_statuses=[STATUS_SENT, STATUS_REPORTED],
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return {"suggestion": _serialize_suggestion(updated)}


async def handle_suggestion_action(
    *,
    request: Request,
    tenant_id: str,
    suggestion_id: str,
    action: str,
    actor_line_id: str,
    draft_message: str | None = None,
    execute_now: bool = False,
) -> dict[str, Any]:
    repo = request.app.state.repository
    suggestion = _require_suggestion(repo, tenant_id, suggestion_id)
    if suggestion.status in {STATUS_DISMISSED, STATUS_EXPIRED, STATUS_REPORTED, STATUS_SENT, STATUS_SENDING}:
        raise HTTPException(status_code=409, detail="suggestion is no longer actionable")

    snapshot = _result_snapshot(suggestion)
    if action == "dismiss":
        snapshot["dismissed_at"] = _utcnow().isoformat()
        snapshot["dismissed_by"] = actor_line_id
        try:
            updated = repo.update_suggestion_status(
                suggestion_id=suggestion_id,
                status=STATUS_DISMISSED,
                result_snapshot=snapshot,
                allowed_current_statuses=[STATUS_PENDING, STATUS_ACCEPTED],
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="invalid suggestion status transition") from exc
        if updated is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        return _serialize_suggestion(updated)

    if draft_message is not None and suggestion.status in EDITABLE_DRAFT_STATUSES:
        suggestion.draft_message = draft_message.strip() or suggestion.draft_message
        suggestion = repo.save_suggestion(suggestion)

    snapshot["accepted_at"] = _utcnow().isoformat()
    snapshot["accepted_by"] = actor_line_id
    try:
        updated = repo.update_suggestion_status(
            suggestion_id=suggestion_id,
            status=STATUS_ACCEPTED,
            result_snapshot=snapshot,
            allowed_current_statuses=[STATUS_PENDING, STATUS_ACCEPTED],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="invalid suggestion status transition") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    if execute_now:
        return await send_suggestion_execution(
            request=request,
            tenant_id=tenant_id,
            suggestion_id=suggestion_id,
            actor_line_id=actor_line_id,
        )
    return _serialize_suggestion(updated)


async def send_suggestion_execution(
    *,
    request: Request,
    tenant_id: str,
    suggestion_id: str,
    actor_line_id: str,
    draft_message: str | None = None,
) -> dict[str, Any]:
    repo = request.app.state.repository
    suggestion = _require_suggestion(repo, tenant_id, suggestion_id)
    if suggestion.status in {STATUS_DISMISSED, STATUS_EXPIRED, STATUS_REPORTED, STATUS_SENT}:
        raise HTTPException(status_code=409, detail="suggestion is no longer actionable")
    if suggestion.status not in {STATUS_PENDING, STATUS_ACCEPTED}:
        raise HTTPException(status_code=409, detail="invalid suggestion status transition")

    if draft_message is not None and suggestion.status in EDITABLE_DRAFT_STATUSES:
        suggestion.draft_message = draft_message.strip() or suggestion.draft_message
        suggestion = repo.save_suggestion(suggestion)

    snapshot = _result_snapshot(suggestion)
    snapshot.setdefault("accepted_at", _utcnow().isoformat())
    snapshot.setdefault("accepted_by", actor_line_id)
    if suggestion.status == STATUS_PENDING:
        try:
            suggestion = repo.update_suggestion_status(
                suggestion_id=suggestion_id,
                status=STATUS_ACCEPTED,
                result_snapshot=snapshot,
                allowed_current_statuses=[STATUS_PENDING],
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail="invalid suggestion status transition") from exc
        if suggestion is None:
            raise HTTPException(status_code=404, detail="suggestion not found")
        snapshot = _result_snapshot(suggestion)
    snapshot["sending_at"] = _utcnow().isoformat()
    snapshot["sending_by"] = actor_line_id
    try:
        suggestion = repo.update_suggestion_status(
            suggestion_id=suggestion_id,
            status=STATUS_SENDING,
            result_snapshot=snapshot,
            allowed_current_statuses=[STATUS_ACCEPTED],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="invalid suggestion status transition") from exc
    if suggestion is None:
        raise HTTPException(status_code=404, detail="suggestion not found")

    execution = await _execute_suggestion(request, suggestion)
    snapshot = _result_snapshot(suggestion)
    snapshot["execution"] = execution
    snapshot["sent_at"] = _utcnow().isoformat()
    snapshot["sent_by"] = actor_line_id
    try:
        updated = repo.update_suggestion_status(
            suggestion_id=suggestion_id,
            status=STATUS_SENT,
            result_snapshot=snapshot,
            allowed_current_statuses=[STATUS_SENDING],
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="invalid suggestion status transition") from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return _serialize_suggestion(updated)


async def _execute_suggestion(request: Request, suggestion) -> dict[str, Any]:
    repo = request.app.state.repository
    dispatcher = request.app.state.execute_dispatcher
    workflow_input_patch = {
        "suggestion_id": suggestion.id,
        "trigger_source": "proactive_suggestion",
        "proactive_reason": suggestion.reason,
        "draft_message": suggestion.draft_message,
    }
    if suggestion.suggestion_type == NUDGE_NO_POST:
        result = await dispatcher.dispatch(
            tenant_id=suggestion.tenant_id,
            text=suggestion.suggested_action,
            intent_label="google_post",
            workflow_input_patch=workflow_input_patch,
        )
        return _persist_agentos_execution(repo, suggestion, result, "kachu_google_post")
    if suggestion.suggestion_type == NUDGE_NEGATIVE_REVIEW:
        result = await dispatcher.dispatch(
            tenant_id=suggestion.tenant_id,
            text=suggestion.suggested_action,
            intent_label="review_reply",
            workflow_input_patch=workflow_input_patch,
        )
        return _persist_agentos_execution(repo, suggestion, result, "kachu_review_reply")
    if suggestion.suggestion_type == NUDGE_SLEEPING_CUSTOMERS:
        settings = request.app.state.settings
        profile_ids = json.loads(suggestion.affected_profile_ids_json or "[]")
        recipients = repo.get_profile_line_user_ids(suggestion.tenant_id, profile_ids)
        if getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") and recipients:
            for recipient in recipients:
                await push_line_messages(
                    to=recipient,
                    messages=[text_message(suggestion.draft_message)],
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            return {
                "mode": "line_push_campaign",
                "workflow": "recover_sleeping_campaign",
                "profile_ids": profile_ids,
                "profile_count": suggestion.profile_count,
                "delivered_count": len(recipients),
                "draft_message": suggestion.draft_message,
            }
        return {
            "mode": "prepared_campaign",
            "workflow": "recover_sleeping_campaign",
            "profile_ids": profile_ids,
            "profile_count": suggestion.profile_count,
            "draft_message": suggestion.draft_message,
        }
    raise HTTPException(status_code=400, detail="unsupported suggestion type")


def _persist_agentos_execution(repo, suggestion, result: ExecutionTaskResult, workflow: str) -> dict[str, Any]:
    suggestion.related_run_id = result.current_run_id or result.task_id
    repo.save_suggestion(suggestion)
    return {
        "mode": "agentos",
        "workflow": workflow,
        "task_id": result.task_id,
        "run_id": result.current_run_id,
        "status": result.status,
        "waiting_approval": result.waiting_approval,
    }


def _require_suggestion(repo, tenant_id: str, suggestion_id: str):
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")
    suggestion = repo.get_suggestion(suggestion_id)
    if suggestion is None or suggestion.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="suggestion not found")
    return suggestion


def _serialize_suggestion(suggestion) -> dict[str, Any]:
    return {
        "id": suggestion.id,
        "suggestion_type": suggestion.suggestion_type,
        "category": suggestion.category,
        "title": suggestion.title,
        "reason": suggestion.reason,
        "body": suggestion.body,
        "profile_count": suggestion.profile_count,
        "suggested_action": suggestion.suggested_action,
        "draft_message": suggestion.draft_message,
        "status": suggestion.status,
        "related_run_id": suggestion.related_run_id,
        "expires_at": _isoformat(suggestion.expires_at),
        "sent_at": _isoformat(suggestion.sent_at),
        "affected_profiles": json.loads(suggestion.affected_profile_ids_json or "[]"),
        "result_snapshot": _result_snapshot(suggestion),
        "payload": json.loads(suggestion.payload_json or "{}"),
    }


def _result_snapshot(suggestion) -> dict[str, Any]:
    return json.loads(suggestion.result_snapshot_json or "{}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _isoformat(value) -> str | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _utc_value(value) -> datetime | None:
    if value is None:
        return None
    if getattr(value, "tzinfo", None) is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)