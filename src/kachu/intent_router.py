from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

import httpx
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .agentOS_client import AgentOSClient
from .goal_parser import GoalParser
from .models import AgentOSTaskRequest, BossRouteDecision, BossRouteMode, Intent
from .persistence import KachuRepository

if TYPE_CHECKING:
    from .policy import KachuExecutionPolicyResolver, PolicyHints

logger = logging.getLogger(__name__)


def _normalize_message_for_idempotency(text: str) -> str:
    return " ".join(text.split())


def _build_knowledge_update_idempotency_key(
    *,
    tenant_id: str,
    boss_message: str,
    line_message_id: str = "",
) -> str:
    if line_message_id:
        return f"{tenant_id}:knowledge_update:line:{line_message_id}"

    message_hash = hashlib.sha1(_normalize_message_for_idempotency(boss_message).encode("utf-8")).hexdigest()[:16]
    trigger_date = datetime.now(timezone.utc).date().isoformat()
    return f"{tenant_id}:knowledge_update:{trigger_date}:{message_hash}"


def _build_business_profile_update_idempotency_key(
    *,
    tenant_id: str,
    boss_message: str,
    line_message_id: str = "",
) -> str:
    if line_message_id:
        return f"{tenant_id}:business_profile_update:line:{line_message_id}"

    message_hash = hashlib.sha1(_normalize_message_for_idempotency(boss_message).encode("utf-8")).hexdigest()[:16]
    trigger_date = datetime.now(timezone.utc).date().isoformat()
    return f"{tenant_id}:business_profile_update:{trigger_date}:{message_hash}"

# ── Keyword shortcuts (fast path, before LLM) ────────────────────────────────

_BUSINESS_PROFILE_UPDATE_KW = frozenset([
    "公休", "店休", "營業時間", "營業到", "打烊", "休息",
    "不營業", "暫停營業", "延後開店", "提早打烊", "今日休", "今天休",
])
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
_META_INSIGHTS_KW = frozenset([
    "FB成效", "臉書成效", "貼文表現", "Facebook成效", "社群成效",
    "FB報告", "臉書報告", "成效報告", "貼文成效", "觸及人數", "曝光次數",
])
_FAQ_KW = frozenset([
    "幾點", "在哪", "怎麼", "有沒有", "多少錢", "開車", "停車", "訂位", "預約",
])
_CONSULT_KW = frozenset([
    "?", "？", "怎麼", "如何", "為什麼", "你覺得", "看法", "建議", "分析", "理解",
    "了解", "評估", "策略", "方向", "定位", "客群", "市場", "先討論",
])
_SMALL_TALK_KW = frozenset([
    "你好", "哈囉", "嗨", "hello", "hi", "早安", "午安", "晚安", "安安", "辛苦了", "謝謝",
])
_WORKFLOW_INTENTS = frozenset({
    Intent.BUSINESS_PROFILE_UPDATE,
    Intent.KNOWLEDGE_UPDATE,
    Intent.GOOGLE_POST,
    Intent.GA4_REPORT,
    Intent.REVIEW_REPLY,
    Intent.META_INSIGHTS,
})

# ── LLM intent classification prompt ─────────────────────────────────────────

_CLASSIFY_SYSTEM = (
    "你是 Kachu 的意圖分類器。根據老闆的訊息，輸出一個 JSON，"
    "格式：{\"intent\": \"...\", \"topic\": \"...\"}\n"
    "intent 只能是以下之一：\n"
    "  photo_content    — 老闆上傳照片，要生成貼文\n"
    "  business_profile_update — 老闆要更新 Google 商家營業資訊或短期營運狀態\n"
    "  knowledge_update — 老闆要修改或新增店家資訊\n"
    "  google_post      — 老闆要發一篇 Google 商家動態（無照片）\n"
    "  ga4_report       — 老闆想看 Google Analytics 流量/業績報告\n"
    "  review_reply     — 老闆要處理顧客評論\n"
    "  meta_insights    — 老闆想看 Facebook/Instagram 成效數據\n"
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
        self._goal_parser = GoalParser(settings)

    # ── Public API ─────────────────────────────────────────────────────────────

    def classify_text(self, text: str) -> Intent:
        """Synchronous keyword-based classification (fast path)."""
        if any(kw in text for kw in _BUSINESS_PROFILE_UPDATE_KW):
            return Intent.BUSINESS_PROFILE_UPDATE
        if any(kw in text for kw in _KNOWLEDGE_UPDATE_KW):
            return Intent.KNOWLEDGE_UPDATE
        if any(kw in text for kw in _GOOGLE_POST_KW):
            return Intent.GOOGLE_POST
        if any(kw in text for kw in _GA4_KW):
            return Intent.GA4_REPORT
        if any(kw in text for kw in _META_INSIGHTS_KW):
            return Intent.META_INSIGHTS
        if any(kw in text for kw in _REVIEW_KW):
            return Intent.REVIEW_REPLY
        if any(kw in text for kw in _FAQ_KW):
            return Intent.FAQ_QUERY
        return Intent.GENERAL_CHAT

    async def plan_boss_message(self, text: str) -> BossRouteDecision:
        intent, topic = await self.classify_text_llm(text)
        mode = BossRouteMode.CONSULT
        small_talk = intent == Intent.GENERAL_CHAT and self._is_small_talk(text)
        if intent in _WORKFLOW_INTENTS:
            if self._is_explicit_execute(text, intent):
                mode = BossRouteMode.EXECUTE
            elif self._looks_like_consult(text):
                mode = BossRouteMode.CONSULT
            else:
                mode = BossRouteMode.CLARIFY

        actions: list[dict[str, str]] = []
        clarify_question: str = ""
        if mode == BossRouteMode.CLARIFY:
            clarify_question = await self._generate_clarify_question(text, intent, topic)
        elif mode == BossRouteMode.CONSULT and not small_talk:
            actions = await self._build_route_actions(
                message=text,
                preferred_intent=intent if intent in _WORKFLOW_INTENTS else None,
                topic=topic,
            )
        return BossRouteDecision(
            mode=mode,
            intent=intent,
            topic=topic,
            actions=actions,
            clarify_question=clarify_question,
            small_talk=small_talk,
        )

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

    async def _generate_clarify_question(self, text: str, intent: Intent, topic: str) -> str:
        """Use LLM to generate a natural-language clarifying question. Falls back to a fixed template."""
        label = self._label_for_intent(intent, topic)
        fallback = f"你說的「{text[:30]}」，是要我{label}，還是你想先聊聊方向？"

        api_key = getattr(self._settings, "GOOGLE_AI_API_KEY", "") if self._settings else ""
        openai_key = getattr(self._settings, "OPENAI_API_KEY", "") if self._settings else ""
        model = getattr(self._settings, "LITELLM_MODEL", "gemini/gemini-3-flash-preview") if self._settings else ""
        if not (api_key or openai_key):
            return fallback

        try:
            from .llm import generate_text
            raw = await generate_text(
                prompt=(
                    f"老闆說：{text}\n"
                    f"系統判斷意圖是：{intent.value}\n"
                    "請用一句繁體中文向老闆確認他的意圖。"
                    "不要用選項或按鈕，就問一個直接的問句，讓他能用一句話回覆你。"
                    "不要超過 40 個字。只輸出問句本身，不要加解釋。"
                ),
                model=model,
                api_key=api_key,
                openai_api_key=openai_key,
            )
            question = raw.strip().splitlines()[0][:80]
            return question if question else fallback
        except Exception:
            return fallback

    def _looks_like_consult(self, text: str) -> bool:
        return any(keyword in text for keyword in _CONSULT_KW)

    def _is_small_talk(self, text: str) -> bool:
        normalized = text.strip().lower().strip("!！?？,.，。~～")
        if not normalized or len(normalized) > 16:
            return False
        if self._looks_like_consult(normalized):
            return False
        return any(keyword in normalized for keyword in _SMALL_TALK_KW)

    def _is_explicit_execute(self, text: str, intent: Intent) -> bool:
        if intent == Intent.BUSINESS_PROFILE_UPDATE:
            return any(kw in text for kw in _BUSINESS_PROFILE_UPDATE_KW) and any(
                token in text for token in ("幫我", "更新", "改", "設成", "今天", "今日")
            )
        if intent == Intent.KNOWLEDGE_UPDATE:
            return any(kw in text for kw in _KNOWLEDGE_UPDATE_KW)
        if intent == Intent.GOOGLE_POST:
            return any(kw in text for kw in _GOOGLE_POST_KW)
        if intent == Intent.GA4_REPORT:
            return any(kw in text for kw in _GA4_KW if kw in {"報告", "週報", "月報"}) or "拉報告" in text or "給我報告" in text
        if intent == Intent.REVIEW_REPLY:
            return any(kw in text for kw in _REVIEW_KW) and any(token in text for token in ("幫我", "回覆", "處理", "回評"))
        if intent == Intent.META_INSIGHTS:
            return True  # 老闆主動查 Meta 成效，直接執行不需確認
        return False

    async def _build_route_actions(
        self,
        *,
        message: str,
        preferred_intent: Intent | None,
        topic: str,
    ) -> list[dict[str, str]]:
        actions = await self._goal_parser.parse(message)
        ordered: list[dict[str, str]] = []

        if preferred_intent in _WORKFLOW_INTENTS:
            preferred_action = {
                "label": self._label_for_intent(preferred_intent, topic),
                "intent": preferred_intent.value,
                "topic": topic,
            }
            ordered.append(preferred_action)

        for action in actions:
            if any(
                existing["intent"] == action["intent"] and existing.get("topic", "") == action.get("topic", "")
                for existing in ordered
            ):
                continue
            ordered.append(action)

        return ordered[:3]

    def _label_for_intent(self, intent: Intent, topic: str) -> str:
        if intent == Intent.BUSINESS_PROFILE_UPDATE:
            return "幫我更新 Google 商家營業資訊"
        if intent == Intent.KNOWLEDGE_UPDATE:
            return "幫我更新這項資訊"
        if intent == Intent.GOOGLE_POST:
            return "幫我產出一篇貼文"
        if intent == Intent.GA4_REPORT:
            return "幫我拉一份流量報告"
        if intent == Intent.REVIEW_REPLY:
            return "幫我處理這則評論"
        if intent == Intent.META_INSIGHTS:
            return "幫我看 Facebook 成效報告"
        return topic[:20] or "幫我接著做"

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
            Intent.BUSINESS_PROFILE_UPDATE: self._dispatch_business_profile_update,
            Intent.KNOWLEDGE_UPDATE: self._dispatch_knowledge_update,
            Intent.GOOGLE_POST: self._dispatch_google_post,
            Intent.GA4_REPORT: self._dispatch_ga4_report,
            Intent.REVIEW_REPLY: self._dispatch_review_reply,
            Intent.META_INSIGHTS: self._dispatch_meta_insights,
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
        line_message_id = str(trigger_payload.get("line_message_id", "")).strip()
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_knowledge_update",
            objective=f"Knowledge update requested by boss: {boss_message[:80]}",
            risk_level="low",
            workflow_input={
                "tenant_id": tenant_id,
                "boss_message": boss_message,
            },
            idempotency_key=_build_knowledge_update_idempotency_key(
                tenant_id=tenant_id,
                boss_message=boss_message,
                line_message_id=line_message_id,
            ),
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="knowledge_update",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_business_profile_update(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        boss_message = trigger_payload.get("message", "")
        line_message_id = str(trigger_payload.get("line_message_id", "")).strip()
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_business_profile_update",
            objective=f"Business profile update requested by boss: {boss_message[:80]}",
            risk_level="low",
            workflow_input={
                "tenant_id": tenant_id,
                "boss_message": boss_message,
            },
            idempotency_key=_build_business_profile_update_idempotency_key(
                tenant_id=tenant_id,
                boss_message=boss_message,
                line_message_id=line_message_id,
            ),
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="business_profile_update",
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
        review_id = trigger_payload.get("review_id", "")
        # When no specific review_id is given, use a timestamped key so AgentOS
        # always creates a fresh task instead of reusing a cached/stuck one.
        if not review_id or review_id == "latest":
            review_id = f"latest_{int(time.time())}"
        task_request = AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_review_reply",
            objective=f"Reply to review {review_id} for tenant {tenant_id}",
            risk_level="medium",
            workflow_input={
                "tenant_id": tenant_id,
                "review_id": review_id,
            },
            idempotency_key=None,
        )
        await self._create_and_run(
            task_request=task_request,
            workflow_type="review_reply",
            tenant_id=tenant_id,
            trigger_source=trigger_source,
            trigger_payload=trigger_payload,
        )

    async def _dispatch_meta_insights(
        self,
        *,
        tenant_id: str,
        trigger_source: str,
        trigger_payload: dict[str, Any],
    ) -> None:
        """Flow A: boss wants Meta (FB/IG) insights — call tool chain directly via internal HTTP."""
        period = trigger_payload.get("period", "week")

        settings = self._settings
        if not settings:
            return

        from .line.push import push_line_messages, text_message

        try:
            base_url = getattr(settings, "KACHU_BASE_URL", "http://localhost:8000")
            api_key = getattr(settings, "KACHU_INTERNAL_API_KEY", "") or getattr(settings, "AGENTOS_API_KEY", "")
            headers = {"X-API-Key": api_key} if api_key else {}
            async with httpx.AsyncClient(timeout=30) as client:
                r1 = await client.post(
                    f"{base_url}/tools/fetch-meta-insights",
                    json={"tenant_id": tenant_id, "period": period},
                    headers=headers,
                )
                r1.raise_for_status()
                raw_insights = r1.json()

                r2 = await client.post(
                    f"{base_url}/tools/generate-meta-insights-summary",
                    json={
                        "tenant_id": tenant_id,
                        "insights_json": json.dumps(raw_insights, ensure_ascii=False),
                    },
                    headers=headers,
                )
                r2.raise_for_status()
                summary_data = r2.json()

                await client.post(
                    f"{base_url}/tools/send-meta-insights-report",
                    json={
                        "tenant_id": tenant_id,
                        "summary": summary_data.get("summary", ""),
                        "details_json": json.dumps(summary_data.get("details", []), ensure_ascii=False),
                    },
                    headers=headers,
                )
        except (httpx.HTTPError, Exception) as exc:
            logger.error("_dispatch_meta_insights failed for tenant=%s: %s", tenant_id, exc)
            if getattr(settings, "LINE_BOSS_USER_ID", "") and getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", ""):
                try:
                    await push_line_messages(
                        to=settings.LINE_BOSS_USER_ID,
                        messages=[text_message("抱歉，暫時無法取得 Facebook 成效，請稍後再試。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                except httpx.HTTPError:
                    pass

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
            logger.error("Dispatch failed for workflow %s: %r", workflow_type, exc)
            self._repo.create_deferred_dispatch(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                task_request=task_request.model_dump(exclude_none=True),
                trigger_source=trigger_source,
                trigger_payload=trigger_payload,
                error=str(exc),
            )
            self._repo.save_audit_event(
                tenant_id=tenant_id,
                workflow_type=workflow_type,
                event_type="dispatch_deferred",
                source="intent_router",
                payload={
                    "trigger_source": trigger_source,
                    "error": str(exc),
                },
            )

