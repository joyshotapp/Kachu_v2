from __future__ import annotations

import logging
from typing import Any

import httpx

from .goal_parser import GoalParser
from .industry_playbook import build_industry_context
from .llm import generate_text

logger = logging.getLogger(__name__)


class BusinessConsultant:
    """Builds a contextual consultant-style reply for boss free-form messages."""

    def __init__(self, repo, memory, settings) -> None:
        self._repo = repo
        self._memory = memory
        self._settings = settings
        self._goal_parser = GoalParser(settings)

    async def build_reply(self, *, tenant_id: str, message: str) -> dict[str, Any]:
        tenant = self._repo.get_or_create_tenant(tenant_id)
        entries = self._repo.get_knowledge_entries(tenant_id)
        ga4_ctx = self._repo.get_shared_context(tenant_id, "ga4_recommendations") or {}
        calendar_ctx = self._repo.get_shared_context(tenant_id, "monthly_content_calendar") or {}
        brand_brief = self._repo.get_shared_context(tenant_id, "brand_brief") or {}
        owner_brief = self._repo.get_shared_context(tenant_id, "owner_brief") or {}
        industry_context = build_industry_context(tenant.industry_type)
        actions = await self._goal_parser.parse(message)

        key_facts = [entry.content for entry in entries if entry.category in {"product", "core_value", "goal"}][:4]
        recent_episodes = self._memory.get_recent_episodes(tenant_id, limit=3)
        diagnosis = await self._generate_diagnosis(
            message=message,
            tenant=tenant,
            industry_context=industry_context,
            key_facts=key_facts,
            ga4_ctx=ga4_ctx,
            calendar_ctx=calendar_ctx,
            brand_brief=brand_brief,
            owner_brief=owner_brief,
            recent_episodes=recent_episodes,
        )

        reply: dict[str, Any] = {
            "type": "text",
            "text": diagnosis,
        }
        quick_reply = self._goal_parser.build_line_quick_reply(actions)
        if quick_reply["items"]:
            reply["quickReply"] = quick_reply
        return reply

    async def _generate_diagnosis(
        self,
        *,
        message: str,
        tenant,
        industry_context: dict[str, Any],
        key_facts: list[str],
        ga4_ctx: dict[str, Any],
        calendar_ctx: dict[str, Any],
        brand_brief: dict[str, Any],
        owner_brief: dict[str, Any],
        recent_episodes: list[dict[str, Any]],
    ) -> str:
        brand_name = tenant.name or "你的品牌"
        industry_name = industry_context.get("industry_name", "一般服務業")
        tone = industry_context.get("recommended_tone", "專業、親切、可信任")
        consultant_focus = "、".join(industry_context.get("consultant_focus", [])) or "把主打價值講清楚"
        market_watchpoints = "、".join(industry_context.get("market_watchpoints", [])) or "訊息一致與轉換效率"
        next_market_event = ""
        market_calendar = industry_context.get("market_calendar", [])
        if market_calendar:
            first_event = market_calendar[0]
            next_market_event = f"本月可優先利用的市場題材是「{first_event.get('name', '')}」"
        ga4_titles = "、".join(item.get("title", "") for item in ga4_ctx.get("recommendations", [])[:2] if item.get("title"))
        brand_summary = brand_brief.get("summary", "")
        owner_priority = "、".join(owner_brief.get("current_priorities", [])[:2])
        episode_summary = "；".join(
            f"{item.get('workflow_type', '')}:{item.get('outcome', '')}"
            for item in recent_episodes[:2]
        )
        calendar_topics = []
        for week in calendar_ctx.get("weeks", [])[:2]:
            topic = week.get("topic", "")
            if topic:
                calendar_topics.append(topic)
        calendar_hint = "、".join(calendar_topics)

        if self._settings.GOOGLE_AI_API_KEY or self._settings.OPENAI_API_KEY:
            try:
                prompt = (
                    "你是 Kachu 的資深行銷顧問，請直接對老闆說話，使用繁體中文，120字以內。\n"
                    "請先用一句話表示你理解他的問題，再給兩個很具體的判斷或建議，最後用一句話引導他點選下一步。\n\n"
                    f"老闆訊息：{message}\n"
                    f"品牌：{brand_name}\n"
                    f"產業：{industry_name}\n"
                    f"品牌調性：{tone}\n"
                    f"已知重點：{'；'.join(key_facts) or '尚未建立完整品牌知識'}\n"
                    f"行業顧問焦點：{consultant_focus}\n"
                    f"市場觀察重點：{market_watchpoints}\n"
                    f"GA4建議：{ga4_titles or '暫無'}\n"
                    f"內容月曆主題：{calendar_hint or '暫無'}\n"
                    f"品牌摘要：{brand_summary or '暫無'}\n"
                    f"老闆近期優先事項：{owner_priority or '暫無'}\n"
                    f"近期工作流結果：{episode_summary or '暫無'}\n"
                    f"市場題材提示：{next_market_event or '暫無'}"
                )
                return (await generate_text(
                    prompt=prompt,
                    model=self._settings.LITELLM_MODEL,
                    api_key=self._settings.GOOGLE_AI_API_KEY,
                    openai_api_key=self._settings.OPENAI_API_KEY,
                )).strip()
            except (httpx.HTTPError, TimeoutError, ModuleNotFoundError) as exc:
                logger.warning("BusinessConsultant LLM failed, using heuristic reply: %s", exc)

        parts = [f"我理解你現在在煩惱「{message[:22]}」這件事。"]
        parts.append(f"以 {industry_name} 來看，現在最該先盯的是 {market_watchpoints}。")
        if key_facts:
            parts.append(f"你目前最能打動客人的重點是 {key_facts[0][:24]}。")
        elif consultant_focus:
            parts.append(f"我建議先補強 {consultant_focus}。")
        if ga4_titles:
            parts.append(f"目前最值得立刻做的是 {ga4_titles}。")
        elif next_market_event:
            parts.append(next_market_event + "。")
        if owner_priority:
            parts.append(f"我會先對齊你最近在意的 {owner_priority}。")
        parts.append("你可以直接點下面的建議，我幫你接著做。")
        return "\n".join(parts)