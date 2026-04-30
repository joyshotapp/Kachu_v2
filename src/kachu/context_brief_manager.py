from __future__ import annotations

from typing import Any

from .industry_playbook import build_industry_context


class ContextBriefManager:
    """Builds durable owner/brand briefs from ongoing tenant context."""

    def __init__(self, repo, memory) -> None:
        self._repo = repo
        self._memory = memory

    async def refresh_briefs(self, tenant_id: str, *, reason: str = "runtime") -> dict[str, dict[str, Any]]:
        tenant = self._repo.get_or_create_tenant(tenant_id)
        entries = self._repo.get_knowledge_entries(tenant_id)
        owner_messages = self._repo.list_recent_conversations(
            tenant_id,
            role="owner",
            conversation_type="general",
            limit=8,
        )
        industry_context = build_industry_context(tenant.industry_type)
        ig_preferences = self._memory.get_preference_examples(tenant_id, "ig_fb", limit=2)
        google_preferences = self._memory.get_preference_examples(tenant_id, "google", limit=2)
        episodes = self._memory.get_recent_episodes(tenant_id, limit=4)

        brand_brief = self._build_brand_brief(
            tenant=tenant,
            entries=entries,
            industry_context=industry_context,
            recent_messages=owner_messages,
            episodes=episodes,
        )
        owner_brief = self._build_owner_brief(
            recent_messages=owner_messages,
            ig_preferences=ig_preferences,
            google_preferences=google_preferences,
            episodes=episodes,
            reason=reason,
        )

        self._repo.save_shared_context(
            tenant_id=tenant_id,
            context_type="brand_brief",
            content=brand_brief,
            ttl_hours=30 * 24,
        )
        self._repo.save_shared_context(
            tenant_id=tenant_id,
            context_type="owner_brief",
            content=owner_brief,
            ttl_hours=30 * 24,
        )
        return {"brand_brief": brand_brief, "owner_brief": owner_brief}

    def _build_brand_brief(
        self,
        *,
        tenant,
        entries: list[Any],
        industry_context: dict[str, Any],
        recent_messages: list[Any],
        episodes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        def _pick(category: str, limit: int = 3) -> list[str]:
            return [entry.content for entry in entries if entry.category == category][:limit]

        products = _pick("product")
        goals = _pick("goal")
        core_values = _pick("core_value")
        style_notes = _pick("style", limit=2)
        recent_focus = [msg.content.strip() for msg in recent_messages if msg.content.strip()][:3]
        recent_outcomes = [item.get("outcome", "") for item in episodes if item.get("outcome")][:3]
        summary_parts = [
            tenant.name or "品牌名稱待補",
            tenant.industry_type or industry_context.get("industry_name", "一般服務業"),
        ]
        if products:
            summary_parts.append(f"主打 {products[0][:24]}")
        if goals:
            summary_parts.append(f"近期目標是 {goals[0][:24]}")

        return {
            "summary": "，".join(summary_parts),
            "brand_name": tenant.name,
            "industry": tenant.industry_type,
            "address": tenant.address,
            "tone": style_notes[0] if style_notes else industry_context.get("recommended_tone", "專業、親切、可信任"),
            "core_values": core_values,
            "products": products,
            "goals": goals,
            "content_angles": industry_context.get("content_angles", []),
            "market_watchpoints": industry_context.get("market_watchpoints", []),
            "recent_focus": recent_focus,
            "recent_outcomes": recent_outcomes,
        }

    def _build_owner_brief(
        self,
        *,
        recent_messages: list[Any],
        ig_preferences: list[dict[str, Any]],
        google_preferences: list[dict[str, Any]],
        episodes: list[dict[str, Any]],
        reason: str,
    ) -> dict[str, Any]:
        messages = [msg.content.strip() for msg in recent_messages if msg.content.strip()]
        recent_topics = messages[:4]
        preference_notes = [item.get("notes", "") for item in [*ig_preferences, *google_preferences] if item.get("notes")][:4]
        edited_examples = [item.get("edited", "") for item in [*ig_preferences, *google_preferences] if item.get("edited")][:2]
        communication_style = "偏好直白、可執行、少空話"
        if any("不要" in text or "先" in text or "直接" in text for text in messages[:3]):
            communication_style = "重視直接決策與明確下一步"
        decision_style = "傾向先看建議摘要，再決定是否執行"
        if any(item.get("outcome") == "modified" for item in episodes):
            decision_style = "會先看草稿，再依細節親自微調"

        return {
            "summary": "；".join(recent_topics[:2]) or "近期尚無新的老闆偏好訊號",
            "communication_style": communication_style,
            "decision_style": decision_style,
            "current_priorities": recent_topics,
            "preference_notes": preference_notes,
            "edited_examples": edited_examples,
            "last_refresh_reason": reason,
        }