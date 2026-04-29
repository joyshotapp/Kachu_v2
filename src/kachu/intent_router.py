from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .agentOS_client import AgentOSClient
from .models import AgentOSTaskRequest, Intent
from .persistence import KachuRepository

if TYPE_CHECKING:
    from .policy import KachuExecutionPolicyResolver, PolicyHints

logger = logging.getLogger(__name__)

# ── Keyword shortcuts (fast path, before LLM) ────────────────────────────────

_KNOWLEDGE_UPDATE_KW = frozenset([
    "改成", "更新", "現在", "調整", "修改", "新增", "刪除", "變更",
    "改為", "改一下", "換成", "不對", "錯了",
])
_GOOGLE_POST_KW = frozenset([
    "寫一篇", "發一篇", "幫我寫", "幫我發", "寫個", "發個", "寫動態", "發動態",
    "商家動態", "活動公告", "限時優惠",
])
_GA4_KW = frozenset([
    "報告", "流量", "數據", "統計", "生意怎樣", "業績", "訪客", "點擊",
    "分析", "週報", "月報",
])
_REVIEW_KW = frozenset([
    "評論", "回覆", "評價", "負評", "好評", "回評",
])
_FAQ_KW = frozenset([
    "幾點", "在哪", "怎麼", "有沒有", "多少錢", "開車", "停車", "訂位", "預約",
])

# ── LLM intent classification prompt ─────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "你是 Kachu 的意圖分類器。根據老闆的訊息，輸出一個 JSON，"
    "格式：{\"intent\": \"...\", \"topic\": \"...\"}\n"
    "intent 只能是以下之一：\n"
    "  photo_content    — 老闆上傳照片，要生成貼文\n"
    "  knowledge_update — 老闆要修改或新增店家資訊\n"
    "  google_post      — 老闆要發一篇 Google 商家動態（無照片）\n"
    "  ga4_report       — 老闆想看流量/業績報告\n"
    "  review_reply     — 老闆要處理顧客評論\n"
    "  faq_query        — 這是顧客在問店家問題（非老闆）\n"
    "  general_chat     — 閒聊或其他\n"
    "topic 是簡短描述（可空字串）。只輸出 JSON。"
)


class IntentRouter:
    """
    Classifies boss LINE messages and dispatches to AgentOS workflows.

    Classification order:
      1. Keyword fast-path (deterministic, zero latency)
      2. LLM classification (async, ~1s)
      3. Fallback: GENERAL_CHAT
    """

    def __init__(
        self,
        agentOS_client: AgentOSClient,
        repository: KachuRepository,
        settings: Any = None,
        policy_resolver: "KachuExecutionPolicyResolver | None" = None,
    ) -> None:
        self._agentOS = agentOS_client
        self._repo = repository
        self._settings = settings
        self._policy_resolver = policy_resolver

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify_text(self, text: str) -> Intent:
        """Synchronous keyword-based classification (fast path)."""
        if any(kw in text for kw in _KNOWLEDGE_UPDATE_KW):
            return Intent.KNOWLEDGE_UPDATE
        if any(kw in text for kw in _GOOGLE_POST_KW):
            return Intent.GOOGLE_POST
        if any(kw in text for kw in _GA4_KW):
            return Intent.GA4_REPORT
        if any(kw in text for kw in _REVIEW_KW):
            return Intent.REVIEW_REPLY
        if any(kw in text for kw in _FAQ_KW):
            return Intent.FAQ_QUERY
        return Intent.GENERAL_CHAT

    async def classify_text_llm(self, text: str) -> tuple[Intent, str]:
        """
        LLM-based classification. Returns (intent, topic).
        Falls back to keyword classify on error.
        """
        if not self._settings:
            return self.classify_text(text), ""

        api_key = getattr(self._settings, "GOOGLE_AI_API_KEY", "")
        openai_key = getattr(self._settings, "OPENAI_API_KEY", "")
        model = getattr(self._settings, "LITELLM_MODEL", "gemini/gemini-3-flash-preview")

        if not (api_key or openai_key):
            return self.classify_text(text), ""

        try:
            from .llm import generate_text
            raw = await generate_text(
                prompt=text,
                system=_CLASSIFY_SYSTEM,
                model=model,
                api_key=api_key,
                openai_api_key=openai_key,
            )
            clean = raw.strip()
            if clean.startswith("```json"):
                clean = clean[7:]
            elif clean.startswith("```"):
                clean = clean[3:]
            if clean.endswith("```"):
                clean = clean[:-3]
            clean = clean.strip()
            result = json.loads(clean)
            intent_str: str = result.get("intent", "general_chat")
            topic: str = result.get("topic", "")
            try:
                intent = Intent(intent_str)
            except ValueError:
                intent = Intent.GENERAL_CHAT
            return intent, topic
        except (httpx.HTTPError, TimeoutError, ModuleNotFoundError, json.JSONDecodeError) as exc:
            logger.warning("LLM intent classification failed, using keyword: %s", exc)
            return self.classify_text(text), ""

    async def dispatch(
        self,
        *,
        intent: Intent,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        dispatch_map = {
            Intent.PHOTO_CONTENT: self._dispatch_photo_content,
            Intent.KNOWLEDGE_UPDATE: self._dispatch_knowledge_update,
            Intent.GOOGLE_POST: self._dispatch_google_post,
            Intent.GA4_REPORT: self._dispatch_ga4_report,
            Intent.REVIEW_REPLY: self._dispatch_review_reply,
            Intent.FAQ_QUERY: self._dispatch_faq,
        }
        handler = dispatch_map.get(intent)
        if handler:
            await handler(
                tenant_id=tenant_id,
                trigger_source=trigger_source,
                trigger_payload=trigger_payload,
            )
        else:
            logger.info("Intent %s → no dispatch (general chat)", intent)

    # ── Dispatch helpers ───────────────────────────────────────────────────────

    async def _dispatch_photo_content(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        line_message_id = trigger_payload.get("line_message_id", "")
        hints = self._resolve_policy_hints(tenant_id)
        workflow_input: dict[str, Any] = {
            "tenant_id": tenant_id,
            "line_message_id": line_message_id,
            "photo_url": trigger_payload.get("photo_url", ""),
        }
        workflow_input.update(hints.to_workflow_input_patch())
        # Phase 5: inject calendar topic hint if available
        calendar = self._repo.get_shared_context(tenant_id, "monthly_content_calendar")
        if calendar and calendar.get("weeks"):
            from datetime import datetime, timezone
            week_of_month = (datetime.now(timezone.utc).day - 1) // 7  # 0-indexed
            week_hint = calendar["weeks"][min(week_of_month, len(calendar["weeks"]) - 1)]
            workflow_input["calendar_topic_hint"] = week_hint.get("topic", "")
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_photo_content",
            objective=f"Process LINE photo message {line_message_id} for tenant {tenant_id}",
            risk_level="medium",
            workflow_input=workflow_input,
            idempotency_key=f"{tenant_id}:{line_message_id}" if line_message_id else None,
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="photo_content",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_knowledge_update(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        boss_message = trigger_payload.get("message", "")
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_knowledge_update",
            objective=f"Knowledge update requested by boss: {boss_message[:80]}",
            risk_level="low",
            workflow_input={
                "tenant_id": tenant_id,
                "boss_message": boss_message,
            },
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="knowledge_update",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_google_post(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        topic = trigger_payload.get("topic", trigger_payload.get("message", ""))
        hints = self._resolve_policy_hints(tenant_id)
        workflow_input: dict[str, Any] = {
            "tenant_id": tenant_id,
            "topic": topic,
            "trigger_source": "boss_request",
        }
        workflow_input.update(hints.to_workflow_input_patch())
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_google_post",
            objective=f"Generate Google Business post: {topic[:80]}",
            risk_level="medium",
            workflow_input=workflow_input,
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="google_post",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_ga4_report(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        period = trigger_payload.get("period", "7daysAgo")
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_ga4_report",
            objective=f"Generate GA4 report for tenant {tenant_id}, period={period}",
            risk_level="low",
            workflow_input={
                "tenant_id": tenant_id,
                "period": period,
                "trigger_source": "boss_request",
            },
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="ga4_report",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_review_reply(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        review_id = trigger_payload.get("review_id", "latest")
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_review_reply",
            objective=f"Reply to review {review_id} for tenant {tenant_id}",
            risk_level="medium",
            workflow_input={
                "tenant_id": tenant_id,
                "review_id": review_id,
            },
            idempotency_key=f"{tenant_id}:{review_id}" if review_id != "latest" else None,
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="review_reply",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_faq(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        customer_line_id = trigger_payload.get("customer_line_id", "")
        message = trigger_payload.get("message", "")
        timestamp = str(int(time.time() * 1000))

        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_line_faq",
            objective=f"Answer customer FAQ from {customer_line_id}",
            risk_level="low",
            workflow_input={
                "tenant_id": tenant_id,
                "customer_line_id": customer_line_id,
                "message": message,
                "message_timestamp": timestamp,
            },
            idempotency_key=f"{tenant_id}:{customer_line_id}:{timestamp}",
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="line_faq",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _resolve_policy_hints(self, tenant_id: str) -> "PolicyHints":
        """Safely resolve policy hints, returning defaults on any error."""
        if self._policy_resolver is None:
            from .policy import PolicyHints
            return PolicyHints()
        return self._policy_resolver.resolve(tenant_id)

    async def _create_and_run(
        self,
        *,
        task_request: AgentOSTaskRequest,
        workflow_type: str,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        try:
            task_view = await self._agentOS.create_task(task_request)
            task_id = task_view.task["id"]
            run_view = await self._agentOS.run_task(task_id)
            run_id = run_view.run["id"]

            self._repo.create_workflow_record(
                tenant_id=tenant_id,
                agentos_run_id=run_id,
                agentos_task_id=task_id,
                workflow_type=workflow_type,
                trigger_source=trigger_source,
                trigger_payload=trigger_payload,
            )
            logger.info(
                "Workflow dispatched: type=%s task_id=%s run_id=%s status=%s",
                workflow_type,
                task_id,
                run_id,
                run_view.run.get("status"),
            )
        except (httpx.HTTPError, ValidationError, SQLAlchemyError) as exc:
            logger.error("Dispatch failed for workflow %s: %s", workflow_type, exc)

