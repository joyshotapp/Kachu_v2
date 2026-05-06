from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .context_brief_manager import ContextBriefManager
    from .memory import MemoryManager
    from .persistence import KachuRepository

logger = logging.getLogger(__name__)


class PostTaskReviewService:
    """Coordinates non-blocking post-task memory and context updates."""

    def __init__(
        self,
        repository: "KachuRepository",
        memory_manager: "MemoryManager",
        context_brief_manager: "ContextBriefManager | None" = None,
    ) -> None:
        self._repo = repository
        self._memory = memory_manager
        self._context_brief_manager = context_brief_manager

    async def after_knowledge_capture(
        self,
        tenant_id: str,
        *,
        reason: str = "knowledge_capture",
    ) -> None:
        await self._refresh_briefs(tenant_id, reason=reason)

    async def after_owner_command_message(
        self,
        tenant_id: str,
        *,
        reason: str = "boss_command_message",
    ) -> None:
        await self._refresh_briefs(tenant_id, reason=reason)

    async def after_preference_update(
        self,
        *,
        tenant_id: str,
        platform: str,
        original_draft: str,
        edited_draft: str,
        run_id: str = "",
        workflow_type: str | None = None,
        outcome: str | None = None,
        context_summary: dict[str, Any] | None = None,
        refresh_reason: str = "preference_update",
        refresh_briefs: bool = True,
    ) -> None:
        if not tenant_id:
            return
        self._store_preference(
            tenant_id=tenant_id,
            platform=platform,
            original_draft=original_draft,
            edited_draft=edited_draft,
            run_id=run_id,
        )
        if workflow_type and outcome:
            self._record_episode(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                outcome=outcome,
                context_summary=context_summary or ({"run_id": run_id} if run_id else {}),
            )
        if refresh_briefs:
            await self._refresh_briefs(tenant_id, reason=refresh_reason)

    async def after_approval_decision(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: dict[str, Any] | None = None,
        refresh_reason: str = "approval_decision",
    ) -> None:
        if not tenant_id:
            return
        self._record_episode(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            outcome=outcome,
            context_summary=context_summary or {},
        )
        self._refresh_approval_profile(tenant_id)
        await self._refresh_briefs(tenant_id, reason=refresh_reason)

    def _store_preference(
        self,
        *,
        tenant_id: str,
        platform: str,
        original_draft: str,
        edited_draft: str,
        run_id: str,
    ) -> None:
        try:
            self._memory.store_preference(
                tenant_id=tenant_id,
                platform=platform,
                original_draft=original_draft,
                edited_draft=edited_draft,
                run_id=run_id,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Preference review update failed (non-blocking): %s", exc)

    def _record_episode(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: dict[str, Any],
    ) -> None:
        try:
            self._memory.record_episode(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                outcome=outcome,
                context_summary=context_summary,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Episode review update failed (non-blocking): %s", exc)

    def _refresh_approval_profile(self, tenant_id: str) -> None:
        if not hasattr(self._repo, "compute_and_save_approval_profile"):
            return
        try:
            self._repo.compute_and_save_approval_profile(tenant_id)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Approval profile refresh failed (non-blocking): %s", exc)

    async def _refresh_briefs(self, tenant_id: str, *, reason: str) -> None:
        if not tenant_id or self._context_brief_manager is None:
            return
        try:
            await self._context_brief_manager.refresh_briefs(tenant_id, reason=reason)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Post-task brief refresh failed for tenant=%s reason=%s: %s",
                tenant_id,
                reason,
                exc,
            )