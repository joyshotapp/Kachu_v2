from __future__ import annotations

from typing import Any

from .conversation_context import (
    COMMAND_CONVERSATION_TYPE,
    extract_brand_name_candidates,
    extract_document_contact_facts,
    extract_document_offer_facts,
    extract_document_product_facts,
    extract_document_restriction_facts,
    extract_document_style_facts,
    is_valid_contact_fact,
    is_valid_offer_fact,
    is_valid_restriction_fact,
    is_low_signal_document_text,
    parse_basic_info_text,
    summarize_document_highlight,
)
from .industry_playbook import build_industry_context


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for item in items:
        cleaned = item.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            results.append(cleaned)
    return results


def _looks_like_execution_command(text: str) -> bool:
    cleaned = text.strip()
    if not cleaned:
        return False
    return cleaned.startswith(("幫我", "請", "麻煩", "可以幫我", "我要", "回覆這則評論"))


class ContextBriefManager:
    """Builds durable owner/brand briefs from ongoing tenant context."""

    def __init__(self, repo, memory) -> None:
        self._repo = repo
        self._memory = memory

    async def refresh_briefs(self, tenant_id: str, *, reason: str = "runtime") -> dict[str, dict[str, Any]]:
        tenant = self._repo.get_or_create_tenant(tenant_id)

        # Only build brand_brief when onboarding is complete — prevents storing
        # corrupted tenant data written during an incomplete onboarding run.
        onboarding_state = self._repo.get_onboarding_state(tenant_id)
        onboarding_complete = getattr(onboarding_state, "step", None) == "completed"

        entries = self._repo.get_active_knowledge_entries(tenant_id)
        if onboarding_complete:
            tenant = self._reconcile_brand_identity(tenant=tenant, entries=entries)
        owner_messages = self._repo.list_recent_conversations(
            tenant_id,
            role="owner",
            conversation_type=COMMAND_CONVERSATION_TYPE,
            limit=8,
        )
        onboarding_messages = self._repo.list_recent_conversations(
            tenant_id,
            role="boss",
            conversation_type="onboarding",
            limit=8,
        )
        owner_messages = sorted(
            [*owner_messages, *onboarding_messages],
            key=lambda message: message.timestamp,
            reverse=True,
        )[:8]
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
        ) if onboarding_complete else {}
        owner_brief = self._build_owner_brief(
            recent_messages=owner_messages,
            ig_preferences=ig_preferences,
            google_preferences=google_preferences,
            episodes=episodes,
            reason=reason,
        )

        if onboarding_complete and brand_brief:
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

    def _reconcile_brand_identity(self, *, tenant, entries: list[Any]):
        basic_info_entries = [entry for entry in entries if entry.category == "basic_info"]
        latest_basic_info = basic_info_entries[0] if basic_info_entries else None
        parsed_basic_info = parse_basic_info_text(latest_basic_info.content) if latest_basic_info else {}

        document_brand_name = self._derive_brand_name_from_documents(entries)
        derived_name = document_brand_name or parsed_basic_info.get("brand_name")
        updates: dict[str, str] = {}
        if derived_name and derived_name != tenant.name:
            updates["name"] = derived_name
        if parsed_basic_info.get("industry") and parsed_basic_info["industry"] != tenant.industry_type:
            updates["industry_type"] = parsed_basic_info["industry"]
        if parsed_basic_info.get("address") and parsed_basic_info["address"] != tenant.address:
            updates["address"] = parsed_basic_info["address"]

        if updates:
            for field, value in updates.items():
                setattr(tenant, field, value)
            tenant = self._repo.save_tenant(tenant)

        desired_basic_info = self._compose_basic_info(tenant)
        if desired_basic_info and (latest_basic_info is None or latest_basic_info.content != desired_basic_info):
            for entry in basic_info_entries:
                self._repo.mark_knowledge_entry_superseded(entry.id)
            self._repo.save_knowledge_entry(
                tenant_id=tenant.id,
                category="basic_info",
                content=desired_basic_info,
                source_type="context_reconciliation",
            )

        return tenant

    def _derive_brand_name_from_documents(self, entries: list[Any]) -> str:
        documents = [
            entry for entry in entries
            if entry.category == "document" and not is_low_signal_document_text(entry.content)
        ]
        for entry in documents[:4]:
            candidates = extract_brand_name_candidates(entry.content)
            if candidates:
                return candidates[0]
        return ""

    def _compose_basic_info(self, tenant) -> str:
        parts = [
            f"店名：{tenant.name}" if tenant.name else "",
            f"行業：{tenant.industry_type}" if tenant.industry_type else "",
            f"地址：{tenant.address}" if tenant.address else "",
        ]
        return "，".join(part for part in parts if part)

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
            values = [entry.content for entry in entries if entry.category == category]
            if category == "contact":
                values = [value for value in values if is_valid_contact_fact(value)]
            elif category == "offer":
                values = [value for value in values if is_valid_offer_fact(value)]
            elif category == "restriction":
                values = [value for value in values if is_valid_restriction_fact(value)]
            return values[:limit]

        document_entries = [
            entry.content for entry in entries
            if entry.category == "document" and not is_low_signal_document_text(entry.content)
        ]
        derived_products: list[str] = []
        derived_contacts: list[str] = []
        derived_style_notes: list[str] = []
        derived_offers: list[str] = []
        derived_restrictions: list[str] = []
        for item in document_entries[:4]:
            derived_products.extend(extract_document_product_facts(item, max_items=3))
            derived_contacts.extend(extract_document_contact_facts(item, max_items=3))
            derived_style_notes.extend(extract_document_style_facts(item, max_items=2))
            derived_offers.extend(extract_document_offer_facts(item, max_items=2))
            derived_restrictions.extend(extract_document_restriction_facts(item, max_items=2))

        products = _dedupe_preserve_order([*_pick("product", limit=6), *derived_products])[:6]
        contact_points = _dedupe_preserve_order([*_pick("contact", limit=4), *derived_contacts])[:4]
        goals = _pick("goal")
        core_values = _pick("core_value")
        document_highlights = _dedupe_preserve_order(
            [summarize_document_highlight(item) for item in document_entries[:4]]
        )[:2]
        style_notes = _dedupe_preserve_order([*_pick("style", limit=2), *derived_style_notes])[:2]
        offers = _dedupe_preserve_order([*_pick("offer", limit=3), *derived_offers])[:3]
        restrictions = _dedupe_preserve_order([*_pick("restriction", limit=3), *derived_restrictions])[:3]
        recent_focus = [
            msg.content.strip()
            for msg in recent_messages
            if msg.content.strip() and not _looks_like_execution_command(msg.content)
        ][:3]
        recent_outcomes = [item.get("outcome", "") for item in episodes if item.get("outcome")][:3]

        # Guard: reject clearly invalid tenant names (questions, multi-value strings)
        tenant_name = tenant.name or ""
        _suspicious = any(ch in tenant_name for ch in ("？", "?", "，", ",")) or (
            len(tenant_name) > 20 and any(w in tenant_name for w in ("建議", "活動", "什麼", "如何", "怎麼"))
        )
        if _suspicious:
            import logging as _log
            _log.getLogger(__name__).warning(
                "Skipping brand_brief: tenant.name looks invalid: %r", tenant_name
            )
            return {}

        summary_parts = [
            tenant_name or "品牌名稱待補",
            tenant.industry_type or industry_context.get("industry_name", "一般服務業"),
        ]
        if products:
            summary_parts.append(f"主打 {products[0][:24]}")
        if goals:
            summary_parts.append(f"近期目標是 {goals[0][:24]}")
        if document_highlights:
            summary_parts.append(f"已匯入 {len(document_highlights)} 份品牌資料")

        return {
            "summary": "，".join(summary_parts),
            "brand_name": tenant.name,
            "industry": tenant.industry_type,
            "address": tenant.address,
            "tone": style_notes[0] if style_notes else industry_context.get("recommended_tone", "專業、親切、可信任"),
            "core_values": core_values,
            "products": products,
            "contact_points": contact_points,
            "offers": offers,
            "restrictions": restrictions,
            "document_highlights": document_highlights,
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
        stable_topics = [text for text in messages if not _looks_like_execution_command(text)]
        recent_topics = (stable_topics or messages)[:4]
        preference_notes = [item.get("notes", "") for item in [*ig_preferences, *google_preferences] if item.get("notes")][:4]
        edited_examples = [
            item.get("edited", "")[:280]
            for item in [*ig_preferences, *google_preferences]
            if item.get("edited")
        ][:2]
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