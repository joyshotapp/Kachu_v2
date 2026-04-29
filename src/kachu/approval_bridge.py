from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .agentOS_client import AgentOSClient
from .config import Settings
from .models import AgentOSApprovalDecision, ApprovalAction
from .persistence import KachuRepository

logger = logging.getLogger(__name__)


class ApprovalBridge:
    """Bridge AgentOS approval state with LINE interactions."""

    def __init__(
        self,
        agentOS_client: AgentOSClient,
        repository: KachuRepository,
        settings: Settings,
    ) -> None:
        self._agentOS = agentOS_client
        self._repo = repository
        self._settings = settings

    async def handle_postback(
        self,
        *,
        run_id: str,
        tenant_id: str,
        action: ApprovalAction,
        actor_line_id: str,
    ) -> None:
        if not run_id:
            logger.warning("Postback received with empty run_id")
            return

        if action == ApprovalAction.EDIT:
            await self._start_edit_session(
                run_id=run_id,
                tenant_id=tenant_id,
                actor_line_id=actor_line_id,
            )
            return

        decision_map = {
            ApprovalAction.APPROVE: "approved",
            ApprovalAction.REJECT: "rejected",
        }
        agentos_decision = decision_map.get(action)
        if agentos_decision is None:
            logger.warning("Unhandled action: %s", action)
            return

        approval_id = await self._agentOS.get_pending_approval_id_for_run(run_id)
        if approval_id is None:
            logger.warning(
                "No pending approval found in AgentOS for run_id=%s (may have already been decided)",
                run_id,
            )
            return

        decision = AgentOSApprovalDecision(
            decision=agentos_decision,
            actor_id=actor_line_id,
        )
        run_view = await self._agentOS.decide_approval(approval_id, decision)

        self._repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision=agentos_decision,
            actor_line_id=actor_line_id,
        )
        pending_rec = self._repo.get_pending_approval_by_run_id(run_id)
        self._repo.save_audit_event(
            tenant_id=tenant_id,
            agentos_run_id=run_id,
            workflow_type=(pending_rec.workflow_type if pending_rec else ""),
            event_type="approval_decided",
            actor_id=actor_line_id,
            source="approval_bridge",
            payload={
                "decision": agentos_decision,
                "new_run_status": run_view.run.get("status"),
            },
        )
        self._record_episodic_memory(
            tenant_id=tenant_id,
            workflow_type=(pending_rec.workflow_type if pending_rec else ""),
            outcome=agentos_decision,
            run_id=run_id,
            log_prefix="Episode record failed",
        )
        self._refresh_approval_profile(tenant_id, "Approval profile refresh failed")

        logger.info(
            "Approval decided: run_id=%s decision=%s new_run_status=%s",
            run_id,
            agentos_decision,
            run_view.run.get("status"),
        )

    async def _start_edit_session(
        self,
        *,
        run_id: str,
        tenant_id: str,
        actor_line_id: str,
    ) -> None:
        """Create an edit session and ask the boss for the corrected IG draft."""
        pending = self._repo.get_pending_approval_by_run_id(run_id)
        ig_draft, google_draft = self._extract_drafts(pending.draft_content if pending else "{}")

        self._repo.create_edit_session(
            tenant_id=tenant_id,
            run_id=run_id,
            ig_draft=ig_draft,
            google_draft=google_draft,
        )
        self._repo.save_audit_event(
            tenant_id=tenant_id,
            agentos_run_id=run_id,
            workflow_type=(pending.workflow_type if pending else ""),
            event_type="edit_session_started",
            actor_id=actor_line_id,
            source="approval_bridge",
            payload={"has_pending": pending is not None},
        )

        if self._settings.LINE_CHANNEL_ACCESS_TOKEN and actor_line_id:
            try:
                from .line.push import push_line_messages, text_message

                await push_line_messages(
                    to=actor_line_id,
                    messages=[
                        text_message(
                            "好的！請輸入修改後的 IG / Facebook 文本：\n"
                            "（如果不需要修改，輸入「跳過」）"
                        )
                    ],
                    access_token=self._settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            except httpx.HTTPError as exc:
                logger.error("Failed to push edit prompt: %s", exc)
        else:
            logger.info(
                "Edit session created for run_id=%s; LINE push skipped (no token)",
                run_id,
            )

    async def complete_edit_and_approve(
        self,
        *,
        run_id: str,
        actor_line_id: str,
        edited_ig_draft: str | None = None,
        edited_google_draft: str | None = None,
    ) -> bool:
        """Submit owner-edited drafts back to AgentOS as an approval decision."""
        approval_id = await self._agentOS.get_pending_approval_id_for_run(run_id)
        if approval_id is None:
            logger.warning(
                "No pending approval found in AgentOS for run_id=%s (EditSession may have expired)",
                run_id,
            )
            return False

        edited_payload: dict[str, str] = {}
        if edited_ig_draft is not None:
            edited_payload["ig_fb"] = edited_ig_draft
        if edited_google_draft is not None:
            edited_payload["google"] = edited_google_draft

        decision = AgentOSApprovalDecision(
            decision="approved",
            actor_id=actor_line_id,
            edited_payload=edited_payload,
        )
        try:
            run_view = await self._agentOS.decide_approval(approval_id, decision)
            logger.info(
                "Edit session completed and approved: run_id=%s new_run_status=%s",
                run_id,
                run_view.run.get("status"),
            )
        except (httpx.HTTPError, ValidationError) as exc:
            logger.error("Failed to complete edit session for run_id=%s: %s", run_id, exc)
            return False

        self._repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision="approved",
            actor_line_id=actor_line_id,
        )
        pending_rec = self._repo.get_pending_approval_by_run_id(run_id)
        tenant_id = pending_rec.tenant_id if pending_rec else ""
        workflow_type = pending_rec.workflow_type if pending_rec else ""
        self._repo.save_audit_event(
            tenant_id=tenant_id,
            agentos_run_id=run_id,
            workflow_type=workflow_type,
            event_type="approval_edited",
            actor_id=actor_line_id,
            source="approval_bridge",
            payload={"edited_fields": sorted(edited_payload.keys())},
        )
        self._record_episodic_memory(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            outcome="edited",
            run_id=run_id,
            log_prefix="Episode record (edit) failed",
        )
        if tenant_id:
            self._refresh_approval_profile(
                tenant_id,
                "Approval profile refresh (edit) failed",
            )
        return True

    def _extract_drafts(self, raw_draft_content: str) -> tuple[str, str]:
        try:
            drafts = json.loads(raw_draft_content)
        except (TypeError, json.JSONDecodeError):
            return "", ""
        return drafts.get("ig_fb", ""), drafts.get("google", "")

    def _record_episodic_memory(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        run_id: str,
        log_prefix: str,
    ) -> None:
        try:
            self._repo.save_episodic_memory(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                outcome=outcome,
                context_summary=json.dumps({"run_id": run_id}, ensure_ascii=False),
            )
        except SQLAlchemyError as exc:
            logger.warning("%s (non-blocking): %s", log_prefix, exc)

    def _refresh_approval_profile(self, tenant_id: str, log_prefix: str) -> None:
        try:
            self._repo.compute_and_save_approval_profile(tenant_id)
        except SQLAlchemyError as exc:
            logger.warning("%s (non-blocking): %s", log_prefix, exc)

