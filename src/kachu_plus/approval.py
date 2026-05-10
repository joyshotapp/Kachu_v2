from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import asyncio
import json
from typing import Any
from zoneinfo import ZoneInfo

from kachu_plus.models import ApprovalAction
from kachu_plus.services import AgentOSTaskDispatcher, PostTaskReviewService


@dataclass
class ApprovalResult:
    run_status: str
    decision: str
    message: str = ""
    scheduled_for: str = ""


def _resolve_scheduled_publish_at(timezone_name: str) -> tuple[datetime, str]:
    normalized_tz = str(timezone_name or "Asia/Taipei").strip() or "Asia/Taipei"
    try:
        zone = ZoneInfo(normalized_tz)
    except Exception:  # noqa: BLE001
        zone = timezone.utc
        normalized_tz = "UTC"

    now_local = datetime.now(zone)
    scheduled_local = now_local.replace(hour=9, minute=0, second=0, microsecond=0)
    if scheduled_local <= now_local + timedelta(minutes=10):
        scheduled_local += timedelta(days=1)
    return scheduled_local.astimezone(timezone.utc), normalized_tz


class ApprovalBridge:
    def __init__(
        self,
        dispatcher: AgentOSTaskDispatcher,
        repo: Any,
        post_task_review: PostTaskReviewService | None = None,
    ) -> None:
        self._dispatcher = dispatcher
        self._repo = repo
        self._post_task_review = post_task_review

    async def handle_postback(
        self,
        *,
        run_id: str,
        tenant_id: str,
        action: ApprovalAction,
        actor_line_id: str,
    ) -> ApprovalResult:
        if action == ApprovalAction.EDIT:
            self._repo.decide_pending_approval(
                agentos_run_id=run_id,
                decision="editing",
                actor_line_id=actor_line_id,
            )
            return ApprovalResult(
                run_status="waiting_edit",
                decision="editing",
                message="好，直接回覆你想怎麼修改。我會先重寫草稿，再推回來給你確認一次。",
            )

        if action == ApprovalAction.SCHEDULE:
            pending = self._repo.get_pending_approval_by_run_id(run_id)
            if pending is None:
                return ApprovalResult(run_status="not_found", decision="missing")
            tenant = self._repo.get_tenant(tenant_id)
            scheduled_at, timezone_name = _resolve_scheduled_publish_at(getattr(tenant, "timezone", "Asia/Taipei"))
            self._repo.decide_pending_approval(
                agentos_run_id=run_id,
                decision="scheduled",
                actor_line_id=actor_line_id,
                decision_payload={
                    "scheduled_for": scheduled_at.isoformat(),
                    "scheduled_timezone": timezone_name,
                },
            )
            try:
                label = scheduled_at.astimezone(ZoneInfo(timezone_name)).strftime("%Y-%m-%d %H:%M")
            except Exception:  # noqa: BLE001
                label = scheduled_at.strftime("%Y-%m-%d %H:%M UTC")
            return ApprovalResult(
                run_status="scheduled",
                decision="scheduled",
                message=f"已排程於 {label} 自動發布。",
                scheduled_for=scheduled_at.isoformat(),
            )

        pending = self._repo.get_pending_approval_by_run_id(run_id)
        approval_id = await self._dispatcher._client.get_pending_approval_id_for_run(run_id)  # noqa: SLF001
        if approval_id is None:
            return ApprovalResult(run_status="not_found", decision="missing")

        decision_map = {
            ApprovalAction.APPROVE: "approved",
            ApprovalAction.REJECT: "rejected",
        }
        agentos_decision = decision_map[action]
        edited_payload = None
        if agentos_decision == "approved" and pending is not None:
            try:
                import json

                edited_payload = json.loads(pending.draft_content or "{}")
            except Exception:  # noqa: BLE001
                edited_payload = None
        run_view = await self._dispatcher.decide_approval(
            approval_id=approval_id,
            decision=agentos_decision,
            actor_id=actor_line_id,
            edited_payload=edited_payload,
        )
        self._repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision=agentos_decision,
            actor_line_id=actor_line_id,
            decision_payload=edited_payload,
        )
        if pending is not None and self._post_task_review is not None:
            await self._post_task_review.after_approval_decision(
                tenant_id=tenant_id,
                workflow_type=pending.workflow_type,
                outcome=agentos_decision,
                context_summary={"run_id": run_id},
            )
        return ApprovalResult(
            run_status=str(run_view.run.get("status", "unknown")),
            decision=agentos_decision,
        )

    async def complete_edit_and_approve(
        self,
        *,
        run_id: str,
        actor_line_id: str,
        edited_payload: dict[str, Any],
    ) -> ApprovalResult:
        pending = self._repo.get_pending_approval_by_run_id(run_id)
        approval_id = await self._dispatcher._client.get_pending_approval_id_for_run(run_id)  # noqa: SLF001
        if approval_id is None:
            return ApprovalResult(run_status="not_found", decision="missing")

        run_view = await self._dispatcher.decide_approval(
            approval_id=approval_id,
            decision="approved",
            actor_id=actor_line_id,
            edited_payload=edited_payload,
        )
        self._repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision="approved",
            actor_line_id=actor_line_id,
            decision_payload=edited_payload,
        )
        if pending is not None:
            try:
                import json

                original_payload = json.loads(pending.draft_content or "{}")
            except Exception:  # noqa: BLE001
                original_payload = {}
            if self._post_task_review is not None:
                if "post_text" in edited_payload and edited_payload["post_text"] != original_payload.get("post_text"):
                    await self._post_task_review.after_preference_update(
                        tenant_id=pending.tenant_id,
                        platform="google",
                        original_draft=str(original_payload.get("post_text", "")),
                        edited_draft=str(edited_payload.get("post_text", "")),
                        run_id=run_id,
                        workflow_type=pending.workflow_type,
                        outcome="edited",
                        context_summary={"run_id": run_id},
                    )
                if "reply_draft" in edited_payload and edited_payload["reply_draft"] != original_payload.get("reply_draft"):
                    await self._post_task_review.after_preference_update(
                        tenant_id=pending.tenant_id,
                        platform="review_reply",
                        original_draft=str(original_payload.get("reply_draft", "")),
                        edited_draft=str(edited_payload.get("reply_draft", "")),
                        run_id=run_id,
                        workflow_type=pending.workflow_type,
                        outcome="edited",
                        context_summary={"run_id": run_id},
                    )
        return ApprovalResult(
            run_status=str(run_view.run.get("status", "unknown")),
            decision="approved",
        )


class ScheduledApprovalService:
    def __init__(self, bridge: ApprovalBridge, repo: Any) -> None:
        self._bridge = bridge
        self._repo = repo

    async def process_due_approvals(self, *, limit: int = 20) -> dict[str, int]:
        due_items = self._repo.list_due_scheduled_approvals(limit=limit)
        approved = 0
        failed = 0
        for pending in due_items:
            result = await self._bridge.handle_postback(
                run_id=pending.agentos_run_id,
                tenant_id=pending.tenant_id,
                action=ApprovalAction.APPROVE,
                actor_line_id="scheduled-publisher",
            )
            if result.decision == "approved":
                approved += 1
            else:
                failed += 1
        return {"due": len(due_items), "approved": approved, "failed": failed}


class ScheduledApprovalScheduler:
    def __init__(self, service: ScheduledApprovalService, *, interval_seconds: int = 60) -> None:
        self._service = service
        self._interval_seconds = max(int(interval_seconds), 60)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> dict[str, int]:
        return await self._service.process_due_approvals()

    async def _run_loop(self) -> None:
        await self.run_once()
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.run_once()


class PendingApprovalSyncService:
    def __init__(self, dispatcher: AgentOSTaskDispatcher, repo: Any) -> None:
        self._dispatcher = dispatcher
        self._repo = repo

    async def sync_open_approvals(self, *, limit: int = 50) -> dict[str, int]:
        pending_items = self._repo.list_pending_approvals(
            statuses=["pending"],
            exclude_workflow_types=["kachu_planned_content", "kachu_meta_reply"],
            limit=limit,
        )
        synced = 0
        skipped = 0
        failed = 0
        for pending in pending_items:
            try:
                run_view = await self._dispatcher._client.get_run(pending.agentos_run_id)  # noqa: SLF001
            except Exception:  # noqa: BLE001
                failed += 1
                continue
            terminal_approval = None
            for approval in reversed(getattr(run_view, "approvals", []) or []):
                decision = str(approval.get("decision", "") or "").strip()
                if decision in {"approved", "rejected"}:
                    terminal_approval = approval
                    break
            if terminal_approval is None:
                skipped += 1
                continue
            self._repo.decide_pending_approval(
                agentos_run_id=pending.agentos_run_id,
                decision=str(terminal_approval.get("decision", "") or "pending"),
                actor_line_id=str(terminal_approval.get("actor_id", "") or "agentos-sync"),
                decision_payload={
                    "synced_from": "agentos",
                    "approval_id": str(terminal_approval.get("id", "") or ""),
                    "run_status": str(getattr(run_view, "run", {}).get("status", "") or ""),
                },
            )
            synced += 1
        return {"checked": len(pending_items), "synced": synced, "skipped": skipped, "failed": failed}


class PendingApprovalSyncScheduler:
    def __init__(self, service: PendingApprovalSyncService, *, interval_seconds: int = 60) -> None:
        self._service = service
        self._interval_seconds = max(int(interval_seconds), 30)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> dict[str, int]:
        return await self._service.sync_open_approvals()

    async def _run_loop(self) -> None:
        await self.run_once()
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.run_once()