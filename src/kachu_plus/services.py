from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx

try:
    import litellm as _litellm_mod
    _LITELLM_AVAILABLE = True
except Exception:  # noqa: BLE001
    _litellm_mod = None  # type: ignore[assignment]
    _LITELLM_AVAILABLE = False

from kachu_plus.agentos_client import AgentOSWorkflowClient
from kachu_plus.config import Settings
from kachu_plus.learning import (
    ContextBriefManager,
    ConversationLearningService,
    IdleBriefRefreshScheduler,
    MemoryManager,
    PostTaskReviewService,
)
from kachu_plus.models import AgentOSApprovalDecision, AgentOSRunView, AgentOSTaskView, ExecutionTaskResult
from kachu_plus.proactive import KachuExecutionPolicyResolver, ProactiveSuggestionEngine

logger = logging.getLogger(__name__)


class UnsupportedExecutionIntentError(ValueError):
    pass


class AgentOSTaskDispatcher:
    def __init__(self, settings: Settings, client: AgentOSWorkflowClient | None = None) -> None:
        self._auto_run = settings.AGENTOS_AUTO_RUN_EXECUTE_TASKS
        self._client = client or AgentOSWorkflowClient(settings.AGENTOS_BASE_URL)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def create_task(
        self,
        *,
        tenant_id: str,
        text: str,
        intent_label: str,
        workflow_input_patch: dict[str, Any] | None = None,
    ) -> tuple[AgentOSTaskView, dict[str, Any]]:
        payload = _build_agentos_task_request(
            tenant_id=tenant_id,
            text=text,
            intent_label=intent_label,
            workflow_input_patch=workflow_input_patch,
        )
        task_view = await self._client.create_task(payload)
        return task_view, payload

    async def get_task(self, task_id: str) -> AgentOSTaskView:
        return await self._client.get_task(task_id)

    async def ensure_run(self, task_id: str) -> AgentOSRunView:
        return await self._client.run_task(task_id)

    async def get_run(self, run_id: str) -> AgentOSRunView:
        return await self._client.get_run(run_id)

    async def list_pending_approvals(self) -> list[dict[str, Any]]:
        return await self._client.list_pending_approvals()

    async def decide_approval(
        self,
        *,
        approval_id: str,
        decision: str,
        actor_id: str,
        edited_payload: dict[str, Any] | None = None,
        edited_payload_ref: str | None = None,
    ) -> AgentOSRunView:
        return await self._client.decide_approval(
            approval_id,
            AgentOSApprovalDecision(
                decision=decision,
                actor_id=actor_id,
                edited_payload=edited_payload,
                edited_payload_ref=edited_payload_ref,
            ),
        )

    async def dispatch(
        self,
        *,
        tenant_id: str,
        text: str,
        intent_label: str,
        workflow_input_patch: dict[str, Any] | None = None,
    ) -> ExecutionTaskResult:
        task_view, payload = await self.create_task(
            tenant_id=tenant_id,
            text=text,
            intent_label=intent_label,
            workflow_input_patch=workflow_input_patch,
        )
        task_id = str(task_view.task["id"])
        task_status = str(task_view.task.get("status", "created"))
        current_run_id = task_view.task.get("current_run_id")
        approval_count = 0
        waiting_approval = False
        status = task_status

        if self._auto_run:
            run_view = await self.ensure_run(task_id)
            current_run_id = run_view.run.get("id")
            status = str(run_view.run.get("status", task_status))
            approval_count = len(run_view.approvals)
            waiting_approval = status == "waiting_approval"

        return ExecutionTaskResult(
            task_id=task_id,
            domain=str(payload["domain"]),
            status=status,
            objective=str(payload["objective"]),
            current_run_id=str(current_run_id) if current_run_id is not None else None,
            waiting_approval=waiting_approval,
            approval_count=approval_count,
        )


class LLMConsultant:
    def __init__(self, settings: Settings) -> None:
        self._model = settings.CONSULTANT_LLM_MODEL or settings.LITELLM_MODEL
        self._google_api_key = settings.GOOGLE_AI_API_KEY
        self._openai_api_key = settings.OPENAI_API_KEY

    async def build_reply(
        self,
        *,
        tenant_name: str,
        industry_type: str,
        message: str,
        context_bundle: dict[str, Any] | None = None,
    ) -> str:
        if self._google_api_key or self._openai_api_key:
            try:
                if _litellm_mod is None:
                    raise ImportError("litellm not installed")

                system = (
                    "你是 Kachu+，是 SMB 老闆的 AI 商業顧問。"
                    "請用繁體中文回答，先理解商家上下文與近期狀態，再做判斷。"
                    "回答格式：先給結論，再補 2 到 3 個理由，最後給一個可執行下一步。"
                )
                context_lines: list[str] = []
                if context_bundle:
                    for key in (
                        "brand_brief",
                        "owner_brief",
                        "conversation_summary_brief",
                        "active_task_brief",
                        "recent_conversations",
                        "relevant_knowledge",
                        "recent_preferences",
                    ):
                        value = context_bundle.get(key)
                        if value:
                            context_lines.append(f"{key}：{json.dumps(value, ensure_ascii=False)}")
                context_block = "\n".join(context_lines)
                prompt = (
                    f"品牌：{tenant_name or '未命名店家'}\n"
                    f"行業：{industry_type or '一般服務業'}\n"
                    + (context_block + "\n" if context_block else "")
                    + f"老闆問題：{message}\n"
                    "請用 120 到 220 字回覆。"
                )
                response = await _litellm_mod.acompletion(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    api_key=_select_api_key(
                        model=self._model,
                        google_api_key=self._google_api_key,
                        openai_api_key=self._openai_api_key,
                    ),
                    max_tokens=2000,
                )
                content = response.choices[0].message.content or ""
                if content.strip():
                    return content.strip()
            except Exception as exc:  # noqa: BLE001
                logger.warning("LLM consult failed, falling back to heuristic reply: %s", exc)

        brand = tenant_name or "你的店"
        summary = ""
        if context_bundle:
            knowledge = context_bundle.get("relevant_knowledge") or []
            if knowledge:
                summary = f"你已知的重要脈絡包含：{knowledge[0]}。"
        industry = industry_type or "你的產業"
        return (
            f"以 {brand} 目前的情況，我會先把重點放在 {industry} 的核心價值說清楚，"
            f"{summary}"
            "先確認你最想改善的是來客、回購還是評價，再選一個最短可驗證的動作開始。"
            "如果你願意，我可以先幫你拆成 3 個優先順序。"
        )


class SleepCustomerQueryService:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def build_reply(self, *, tenant_id: str, text: str) -> str:
        tenant = self._repo.get_tenant(tenant_id)
        threshold = _extract_sleep_days(text, tenant.sleep_threshold if tenant is not None else 60)
        profiles = self._repo.list_sleeping_customer_profiles(
            tenant_id,
            minimum_days=threshold,
            limit=10,
        )
        if not profiles:
            return f"目前沒有超過 {threshold} 天沒互動的顧客。"

        lines = [f"目前有 {len(profiles)} 位顧客超過 {threshold} 天沒回來："]
        for index, profile in enumerate(profiles[:5], start=1):
            name = profile.custom_name or profile.display_name or f"未命名顧客 {index}"
            lines.append(f"{index}. {name}，{profile.sleep_since_days} 天未互動")
        if len(profiles) > 5:
            lines.append(f"另外還有 {len(profiles) - 5} 位顧客符合條件。")
        return "\n".join(lines)


class SleepProfileSyncService:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def sync_tenant(self, tenant_id: str, *, now: datetime | None = None) -> int:
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is None:
            return 0

        now = now or datetime.now(timezone.utc)
        updated = 0
        for profile in self._repo.list_customer_profiles_for_tenant(tenant_id):
            days = _days_since(profile.last_interaction_at, now)
            profile.sleep_since_days = days

            if profile.status not in {"blacklisted", "churned"}:
                profile.status = "sleeping" if days >= tenant.sleep_threshold else "active"

            self._repo.save_customer_profile(profile)
            updated += 1

        logger.info("tenant=%s sleep profile sync updated=%s", tenant_id, updated)
        return updated

    def sync_all_tenants(self, *, now: datetime | None = None) -> dict[str, int]:
        summary: dict[str, int] = {}
        for tenant in self._repo.list_active_tenants():
            summary[tenant.id] = self.sync_tenant(tenant.id, now=now)
        return summary


class SleepSyncScheduler:
    def __init__(self, service: SleepProfileSyncService, settings: Settings) -> None:
        self._service = service
        self._enabled = settings.SLEEP_SYNC_ENABLED
        self._run_on_startup = settings.SLEEP_SYNC_RUN_ON_STARTUP
        self._interval_seconds = max(int(settings.SLEEP_SYNC_INTERVAL_SECONDS), 60)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self._enabled or self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())
        logger.info("SleepSyncScheduler started interval_seconds=%s", self._interval_seconds)

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None
        logger.info("SleepSyncScheduler shut down")

    async def run_once(self) -> dict[str, int]:
        return self._service.sync_all_tenants()

    async def _run_loop(self) -> None:
        if self._run_on_startup:
            await self.run_once()
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.run_once()


def _build_agentos_task_request(
    *,
    tenant_id: str,
    text: str,
    intent_label: str,
    workflow_input_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_text = " ".join(text.split())
    if intent_label == "photo_content":
        domain = "kachu_photo_content"
        objective = "Generate social post drafts from the boss photo upload"
        workflow_input = {
            "tenant_id": tenant_id,
            "trigger_source": "boss_photo_upload",
        }
    elif intent_label == "google_post":
        domain = "kachu_google_post"
        objective = f"Create a Google post draft for: {normalized_text}"
        workflow_input = {
            "tenant_id": tenant_id,
            "topic": normalized_text,
            "trigger_source": "boss_request",
        }
    elif intent_label == "review_reply":
        domain = "kachu_review_reply"
        objective = f"Prepare a review reply task for: {normalized_text}"
        workflow_input = {
            "tenant_id": tenant_id,
            "review_id": "pending_from_line",
            "boss_message": normalized_text,
        }
    elif intent_label == "analytics_report":
        domain = "kachu_ga4_report"
        objective = "Generate an analytics summary requested by the boss"
        workflow_input = {
            "tenant_id": tenant_id,
            "period": "30daysAgo",
            "trigger_source": "boss_request",
        }
    elif intent_label in {"knowledge_update", "business_profile_update"}:
        domain = "kachu_knowledge_update"
        objective = f"Apply a knowledge update requested by the boss: {normalized_text}"
        workflow_input = {
            "tenant_id": tenant_id,
            "boss_message": normalized_text,
            "trigger_source": "boss_request",
        }
    else:
        raise UnsupportedExecutionIntentError(f"intent '{intent_label}' is not mapped to an AgentOS workflow yet")

    digest = hashlib.sha256(f"{tenant_id}:{intent_label}:{normalized_text}".encode("utf-8")).hexdigest()[:16]
    workflow_input.update(workflow_input_patch or {})
    return {
        "tenant_id": tenant_id,
        "domain": domain,
        "objective": objective,
        "workflow_input": workflow_input,
        "idempotency_key": f"boss:{intent_label}:{digest}",
    }


def _select_api_key(*, model: str, google_api_key: str, openai_api_key: str) -> str:
    if model.startswith("gemini/"):
        return google_api_key
    if model.startswith("gpt") or model.startswith("openai/"):
        return openai_api_key
    return google_api_key or openai_api_key


def _extract_sleep_days(text: str, default_days: int) -> int:
    normalized = text.strip()
    match = re.search(r"(\d+)\s*天", normalized)
    if match:
        value = int(match.group(1))
        if value > 0:
            return value

    if "每週" in normalized or "一週" in normalized or "每周" in normalized:
        return 7
    if "每兩週" in normalized or "兩週" in normalized:
        return 14
    if "半個月" in normalized:
        return 15
    if "每月" in normalized or "一個月" in normalized or "一月" in normalized:
        return 30
    if "兩個月" in normalized or "每兩個月" in normalized:
        return 60

    fallback = re.search(r"\d+", normalized)
    if fallback:
        value = int(fallback.group())
        if value > 0:
            return value
    return default_days


def _days_since(last_interaction_at: datetime | None, now: datetime) -> int:
    if last_interaction_at is None:
        return 0
    current = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    previous = (
        last_interaction_at
        if last_interaction_at.tzinfo is not None
        else last_interaction_at.replace(tzinfo=timezone.utc)
    )
    delta = current - previous
    return max(delta.days, 0)