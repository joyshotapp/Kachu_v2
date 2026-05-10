from __future__ import annotations

import json
from typing import Any

from kachu_plus.industry_playbook import build_industry_context
from kachu_plus.website_knowledge import select_knowledge_highlights


def _compute_diff_notes(original: str, edited: str) -> str:
    if original == edited:
        return "無修改"

    notes: list[str] = []
    if len(edited) > len(original):
        notes.append("老闆補充了更多內容")
    else:
        notes.append("老闆調整了用詞")
    if "！" in edited and "！" not in original:
        notes.append("老闆加了感嘆號")
    if "#" in edited and "#" not in original:
        notes.append("老闆加了 hashtag")
    return "；".join(notes)


def _actor_label(actor_role: str) -> str:
    return {
        "boss": "老闆",
        "ai": "Kachu",
        "customer": "顧客",
        "platform": "平台",
    }.get(actor_role, actor_role or "未知角色")


class MemoryManager:
    def __init__(self, repo: Any, settings: Any) -> None:
        self._repo = repo
        self._settings = settings

    def store_preference(
        self,
        *,
        tenant_id: str,
        platform: str,
        original_draft: str,
        edited_draft: str,
        run_id: str = "",
    ) -> None:
        self._repo.save_preference_memory(
            tenant_id=tenant_id,
            platform=platform,
            original_draft=original_draft,
            edited_draft=edited_draft,
            diff_notes=_compute_diff_notes(original_draft, edited_draft),
            run_id=run_id,
        )

    def get_preference_examples(self, tenant_id: str, platform: str, limit: int = 3) -> list[dict[str, Any]]:
        rows = self._repo.get_preference_memories(tenant_id, platform=platform, limit=limit)
        return [
            {
                "original": row.original_draft,
                "edited": row.edited_draft,
                "notes": row.diff_notes,
            }
            for row in rows
        ]

    def record_episode(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: dict[str, Any],
    ) -> None:
        self._repo.save_episodic_memory(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            outcome=outcome,
            context_summary=json.dumps(context_summary, ensure_ascii=False),
        )

    def get_recent_episodes(self, tenant_id: str, workflow_type: str | None = None, limit: int = 5) -> list[dict[str, Any]]:
        rows = self._repo.get_episodic_memories(tenant_id, workflow_type=workflow_type, limit=limit)
        return [
            {
                "workflow_type": row.workflow_type,
                "outcome": row.outcome,
                "created_at": row.created_at.isoformat(),
            }
            for row in rows
        ]


class ContextBriefManager:
    def __init__(self, repo: Any, memory: MemoryManager) -> None:
        self._repo = repo
        self._memory = memory

    async def refresh_briefs(self, tenant_id: str, *, reason: str = "runtime") -> dict[str, dict[str, Any]]:
        tenant = self._repo.get_tenant(tenant_id)
        knowledge = self._repo.list_knowledge_entries(tenant_id, limit=12)
        owner_examples = self._memory.get_preference_examples(tenant_id, "google", limit=2)
        episodes = self._memory.get_recent_episodes(tenant_id, limit=4)
        recent_conversations = self._repo.list_recent_conversations(tenant_id, limit=6)
        active_task = self._repo.get_latest_execute_task_for_tenant(tenant_id)
        industry_context = build_industry_context(tenant.industry_type if tenant is not None else "")
        brand_brief = {
            "brand_name": tenant.name if tenant is not None else "",
            "industry": tenant.industry_type if tenant is not None else "",
            "address": tenant.address if tenant is not None else "",
            "tone": industry_context["recommended_tone"],
            "knowledge_highlights": select_knowledge_highlights(knowledge, limit=3),
            "industry_context": industry_context,
        }
        owner_brief = {
            "current_priorities": select_knowledge_highlights(knowledge, limit=2),
            "preference_examples": owner_examples,
            "recent_outcomes": [episode["outcome"] for episode in episodes],
            "consultant_focus": industry_context["consultant_focus"],
            "reason": reason,
        }
        recent_turns = [
            f"{_actor_label(getattr(row, 'actor_role', ''))}：{str(getattr(row, 'content_text', '') or '')[:80]}"
            for row in reversed(recent_conversations)
            if str(getattr(row, "content_text", "") or "").strip()
        ]
        conversation_summary_brief = {
            "summary": " | ".join(recent_turns[:4]) if recent_turns else "尚無近期對話摘要",
            "recent_turns": recent_turns,
            "reason": reason,
        }
        active_task_brief = {
            "task_id": getattr(active_task, "task_id", "") if active_task is not None else "",
            "run_id": getattr(active_task, "run_id", "") if active_task is not None else "",
            "intent_label": getattr(active_task, "intent_label", "") if active_task is not None else "",
            "status": getattr(active_task, "status", "") if active_task is not None else "",
            "objective": getattr(active_task, "objective", "") if active_task is not None else "",
        }
        customer_brief = {
            "sleeping_customer_count": len(self._repo.list_sleeping_customer_profiles(tenant_id, minimum_days=30, limit=10)),
            "customer_motivations": industry_context["customer_motivations"],
        }
        self._repo.save_context_brief(tenant_id=tenant_id, brief_type="brand_brief", content=brand_brief, ttl_hours=30 * 24)
        self._repo.save_context_brief(tenant_id=tenant_id, brief_type="owner_brief", content=owner_brief, ttl_hours=30 * 24)
        self._repo.save_context_brief(tenant_id=tenant_id, brief_type="conversation_summary_brief", content=conversation_summary_brief, ttl_hours=24)
        self._repo.save_context_brief(tenant_id=tenant_id, brief_type="active_task_brief", content=active_task_brief, ttl_hours=12)
        self._repo.save_context_brief(tenant_id=tenant_id, brief_type="customer_brief", content=customer_brief, ttl_hours=30 * 24)
        return {
            "brand_brief": brand_brief,
            "owner_brief": owner_brief,
            "conversation_summary_brief": conversation_summary_brief,
            "active_task_brief": active_task_brief,
            "customer_brief": customer_brief,
        }


class PostTaskReviewService:
    def __init__(self, repository: Any, memory_manager: MemoryManager, context_brief_manager: ContextBriefManager | None = None) -> None:
        self._repo = repository
        self._memory = memory_manager
        self._context_brief_manager = context_brief_manager

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
    ) -> None:
        self._memory.store_preference(
            tenant_id=tenant_id,
            platform=platform,
            original_draft=original_draft,
            edited_draft=edited_draft,
            run_id=run_id,
        )
        if workflow_type and outcome:
            self._memory.record_episode(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                outcome=outcome,
                context_summary=context_summary or {"run_id": run_id},
            )
        if self._context_brief_manager is not None:
            await self._context_brief_manager.refresh_briefs(tenant_id, reason=refresh_reason)

    async def after_approval_decision(
        self,
        *,
        tenant_id: str,
        workflow_type: str,
        outcome: str,
        context_summary: dict[str, Any] | None = None,
        refresh_reason: str = "approval_decision",
    ) -> None:
        self._memory.record_episode(
            tenant_id=tenant_id,
            workflow_type=workflow_type,
            outcome=outcome,
            context_summary=context_summary or {},
        )
        self._repo.compute_and_save_approval_profile(tenant_id)
        if self._context_brief_manager is not None:
            await self._context_brief_manager.refresh_briefs(tenant_id, reason=refresh_reason)