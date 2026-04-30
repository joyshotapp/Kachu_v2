"""
Phase 5: ContentCalendarAgent

Runs on the 1st of each month. Uses recent episodes + GA4 SharedContext
to generate a lightweight monthly content calendar and pushes it to the boss
via LINE.

The calendar is stored in SharedContext (type: monthly_content_calendar)
so other workflows can read it as topic hints during the month.
"""
from __future__ import annotations

import inspect
import json
import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy.exc import SQLAlchemyError

from .industry_playbook import build_industry_context
from .llm import generate_text
from .memory.manager import MemoryManager
from .persistence import KachuRepository

logger = logging.getLogger(__name__)

_CALENDAR_CONTEXT_TYPE = "monthly_content_calendar"

_CALENDAR_PROMPT = """\
你是一個 {industry_name} 品牌顧問。請根據以下資訊，為品牌擬定本月（{month}）的 4 週行銷貼文建議。

過去的 GA4 關鍵字建議：
{ga4_hints}

近期觀察（來自老闆的批改記錄）：
{episode_hints}

品牌偏好筆記：
{preference_hints}

產業常用題材：
{industry_angles}

市場題材提醒：
{market_calendar}

請輸出 JSON 格式如下（純 JSON，不要任何說明文字）：
{{
  "weeks": [
    {{"week": 1, "theme": "...", "channel": "ig_fb", "topic": "..."}},
    {{"week": 2, "theme": "...", "channel": "google", "topic": "..."}},
    {{"week": 3, "theme": "...", "channel": "ig_fb", "topic": "..."}},
    {{"week": 4, "theme": "...", "channel": "google", "topic": "..."}}
  ]
}}"""


async def _resolve_maybe_awaitable(value):
    if inspect.isawaitable(value):
        return await value
    return value


class ContentCalendarAgent:
    """
    Generates a monthly content calendar for a tenant and persists it
    in SharedContext so photo_content and google_post workflows can pick
    it up as suggested topics.
    """

    def __init__(
        self,
        repo: KachuRepository,
        memory: MemoryManager,
        settings,
    ) -> None:
        self._repo = repo
        self._memory = memory
        self._settings = settings

    async def generate_and_save(self, tenant_id: str, run_id: str = "") -> dict:
        """Generate this month's calendar and save to SharedContext. Return calendar dict."""
        month = datetime.now(timezone.utc).strftime("%Y年%m月")
        tenant = self._repo.get_or_create_tenant(tenant_id)
        industry_context = build_industry_context(tenant.industry_type)

        # Gather context
        ga4_ctx = self._repo.get_shared_context(tenant_id, "ga4_recommendations") or {}
        ga4_hints = json.dumps(ga4_ctx.get("recommendations", []), ensure_ascii=False) or "（尚無 GA4 資料）"

        episodes = await _resolve_maybe_awaitable(
            self._memory.get_recent_episodes(tenant_id, workflow_type="", limit=5)
        )
        episode_hints = "\n".join(
            f"- {ep.get('outcome','?')}: {ep.get('context_summary', '')[:80]}"
            for ep in episodes
        ) or "（尚無歷史記錄）"

        ig_prefs = await _resolve_maybe_awaitable(
            self._memory.get_preference_examples(tenant_id, "ig_fb", limit=2)
        )
        preference_hints = "\n".join(str(p) for p in ig_prefs) or "（尚無偏好記錄）"
        industry_angles = "、".join(industry_context.get("content_angles", [])) or "（尚無）"
        market_calendar = "\n".join(
            f"- {item.get('name', '')}：{item.get('focus', '')}"
            for item in industry_context.get("market_calendar", [])
        ) or "（尚無）"

        prompt = _CALENDAR_PROMPT.format(
            industry_name=industry_context.get("industry_name", "一般服務業"),
            month=month,
            ga4_hints=ga4_hints,
            episode_hints=episode_hints,
            preference_hints=preference_hints,
            industry_angles=industry_angles,
            market_calendar=market_calendar,
        )

        calendar: dict = {}
        try:
            raw = await generate_text(
                prompt=prompt,
                model=self._settings.LITELLM_MODEL,
                api_key=self._settings.GOOGLE_AI_API_KEY,
                openai_api_key=self._settings.OPENAI_API_KEY,
            )
            # Strip markdown fences if present
            clean = raw.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            calendar = json.loads(clean)
        except (httpx.HTTPError, TimeoutError, ModuleNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("ContentCalendar LLM failed for tenant=%s: %s", tenant_id, exc)
            calendar = {
                "weeks": [
                    {"week": i, "theme": "一般行銷", "channel": ch, "topic": ""}
                    for i, ch in enumerate(
                        ["ig_fb", "google", "ig_fb", "google"], start=1
                    )
                ]
            }

        # Persist to SharedContext (expires in 35 days)
        self._repo.save_shared_context(
            tenant_id=tenant_id,
            context_type=_CALENDAR_CONTEXT_TYPE,
            content=calendar,
            source_run_id=run_id,
            ttl_hours=35 * 24,
        )
        logger.info("ContentCalendar saved for tenant=%s month=%s", tenant_id, month)
        return calendar

    async def scan_all_tenants(self) -> None:
        """Called by scheduler on the 1st of each month."""
        tenant_ids = self._repo.list_active_tenant_ids()
        for tenant_id in tenant_ids:
            try:
                await self.generate_and_save(tenant_id)
            except SQLAlchemyError as exc:
                logger.error("ContentCalendar failed for tenant=%s: %s", tenant_id, exc)
