from __future__ import annotations

from typing import Any

from kachu_plus.learning import ContextBriefManager, MemoryManager
from kachu_plus.retrieval_plan import RetrievalPlanComposer
from kachu_plus.website_knowledge import select_knowledge_highlights


def _serialize_conversation(row: Any) -> dict[str, Any]:
    return {
        "actor_role": str(getattr(row, "actor_role", "") or ""),
        "conversation_kind": str(getattr(row, "conversation_kind", "") or ""),
        "content_text": str(getattr(row, "content_text", "") or ""),
        "created_at": getattr(row, "created_at", None).isoformat() if getattr(row, "created_at", None) else "",
    }


class ConsultContextBuilder:
    def __init__(self, repo: Any, memory: MemoryManager, briefs: ContextBriefManager) -> None:
        self._repo = repo
        self._memory = memory
        self._briefs = briefs
        self._retrieval = RetrievalPlanComposer(repo, memory)

    async def build_bundle(
        self,
        *,
        tenant_id: str,
        message: str,
        line_user_id: str = "",
    ) -> dict[str, Any]:
        brief_payloads = await self._briefs.refresh_briefs(tenant_id, reason="consult")
        retrieval_plan = self._retrieval.compose(
            tenant_id=tenant_id,
            query=message,
            line_user_id=line_user_id,
            workflow_type="consult",
        )
        recent_conversations = retrieval_plan.get("recent_conversations", [])
        relevant_knowledge = [row.get("content", "") for row in retrieval_plan.get("persistent_knowledge", [])[:6]]
        if not relevant_knowledge:
            knowledge_entries = self._repo.list_knowledge_entries(tenant_id, limit=12)
            relevant_knowledge = select_knowledge_highlights(knowledge_entries, limit=6)

        return {
            "message": message,
            "brand_brief": brief_payloads.get("brand_brief", {}),
            "owner_brief": brief_payloads.get("owner_brief", {}),
            "conversation_summary_brief": brief_payloads.get("conversation_summary_brief", {}),
            "active_task_brief": brief_payloads.get("active_task_brief", {}),
            "customer_brief": brief_payloads.get("customer_brief", {}),
            "recent_conversations": recent_conversations or [
                _serialize_conversation(row)
                for row in self._repo.list_recent_conversations(
                    tenant_id,
                    limit=6,
                    line_user_id=line_user_id or None,
                )
            ],
            "relevant_knowledge": relevant_knowledge,
            "recent_preferences": retrieval_plan.get("preference_examples", []) or self._memory.get_preference_examples(tenant_id, "google", limit=3),
            "episodes": retrieval_plan.get("episodes", []),
            "retrieval_plan": retrieval_plan,
        }