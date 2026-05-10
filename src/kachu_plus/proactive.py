from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Any

from kachu_plus.line.push import push_line_messages, resolve_tenant_line_recipients, suggestion_card_message
from kachu_plus.meta import MetaConnectorError, MetaInsightsService, deliver_meta_insights_report, summarize_meta_insights


NUDGE_NO_POST = "content_gap"
NUDGE_NEGATIVE_REVIEW = "unanswered_reviews"
NUDGE_STALE_KNOWLEDGE = "stale_knowledge_base"
NUDGE_SLEEPING_CUSTOMERS = "recover_sleeping"
PROACTIVE_SCAN_JOB = "daily_proactive_scan"
META_INSIGHTS_REPORT_JOB = "daily_meta_insights_report"


class KachuExecutionPolicyResolver:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def resolve(self, tenant_id: str) -> dict[str, Any]:
        profile = self._repo.get_approval_profile(tenant_id)
        if profile is None or profile.total_decisions < 3:
            return {
                "approval_timeout_seconds": 86400,
                "require_direction_check": False,
                "policy_generation_context": "",
            }
        if profile.recent_acceptance_rate >= 0.85 and profile.median_edit_delta < 0.1:
            return {
                "approval_timeout_seconds": 21600,
                "require_direction_check": False,
                "policy_generation_context": "",
            }
        if profile.recent_acceptance_rate < 0.5:
            return {
                "approval_timeout_seconds": 86400,
                "require_direction_check": True,
                "policy_generation_context": "【注意：老闆最近多次拒絕草稿，請避免制式語氣。】",
            }
        return {
            "approval_timeout_seconds": 86400,
            "require_direction_check": False,
            "policy_generation_context": "",
        }


class ProactiveSuggestionEngine:
    def __init__(
        self,
        repo: Any,
        settings: Any | None = None,
        *,
        consultant: Any | None = None,
        meta_service: MetaInsightsService | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._consultant = consultant
        self._meta_service = meta_service

    def detect_nudge(self, tenant_id: str) -> str | None:
        now = datetime.now(timezone.utc)
        last_published = self._repo.get_last_published_at(tenant_id)
        if last_published is not None and last_published.tzinfo is None:
            last_published = last_published.replace(tzinfo=timezone.utc)
        if last_published is None or (now - last_published) > timedelta(days=7):
            return NUDGE_NO_POST
        if self._repo.get_pending_negative_reviews(tenant_id) > 0:
            return NUDGE_NEGATIVE_REVIEW
        knowledge_updated_at = self._repo.get_knowledge_last_updated_at(tenant_id)
        if knowledge_updated_at is None or (
            (knowledge_updated_at.replace(tzinfo=timezone.utc) if knowledge_updated_at.tzinfo is None else knowledge_updated_at)
            <= now - timedelta(days=60)
        ):
            return NUDGE_STALE_KNOWLEDGE
        tenant = self._repo.get_tenant(tenant_id)
        threshold = getattr(tenant, "sleep_threshold", 60) if tenant is not None else 60
        sleeping = self._repo.list_sleeping_customer_profiles(tenant_id, minimum_days=threshold, limit=5)
        if sleeping:
            return NUDGE_SLEEPING_CUSTOMERS
        return None

    def run_once_for_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        self._repo.expire_due_suggestions(tenant_id)
        suggestion_type = self.detect_nudge(tenant_id)
        if suggestion_type is None:
            return None
        existing = self._repo.get_latest_active_suggestion(tenant_id, suggestion_type)
        if existing is not None:
            return self._serialize_suggestion(existing)

        now = datetime.now(timezone.utc)
        title_map = {
            NUDGE_NO_POST: "最近 7 天沒有新貼文",
            NUDGE_NEGATIVE_REVIEW: "有待回覆的負評",
            NUDGE_STALE_KNOWLEDGE: "知識庫已超過 60 天未更新",
            NUDGE_SLEEPING_CUSTOMERS: "有一批沉睡顧客可喚回",
        }
        reason_map = {
            NUDGE_NO_POST: "品牌陣地已連續 7 天沒有新貼文，曝光會持續下滑。",
            NUDGE_NEGATIVE_REVIEW: "目前存在至少一則未處理負評，持續暴露會影響轉換。",
            NUDGE_STALE_KNOWLEDGE: "知識庫已超過 60 天未更新，之後的諮詢與草稿會逐漸脫離現況。",
            NUDGE_SLEEPING_CUSTOMERS: "有一批顧客超過商家設定門檻未互動，已接近流失。",
        }
        body_map = {
            NUDGE_NO_POST: "建議先補一篇本週 Google 商家動態，維持品牌存在感。",
            NUDGE_NEGATIVE_REVIEW: "建議優先起草回覆，先止血再觀察後續評價變化。",
            NUDGE_STALE_KNOWLEDGE: "建議先補最新營業資訊、主打商品或檔期內容，避免後續生成沿用過時資訊。",
            NUDGE_SLEEPING_CUSTOMERS: "建議先發一則溫和的回流訊息，測試沉睡顧客回應率。",
        }
        action_map = {
            NUDGE_NO_POST: "發一篇本週 Google 商家動態",
            NUDGE_NEGATIVE_REVIEW: "起草並回覆未處理負評",
            NUDGE_STALE_KNOWLEDGE: "更新目前的店家知識與主打資訊",
            NUDGE_SLEEPING_CUSTOMERS: "發送一則好久不見喚回訊息",
        }
        draft_map = {
            NUDGE_NO_POST: "本週新消息已準備好，歡迎回來看看我們這次的新亮點。",
            NUDGE_NEGATIVE_REVIEW: "謝謝你的回饋，我們已注意到這次體驗不如預期，會立即改善。",
            NUDGE_STALE_KNOWLEDGE: "最近店裡有一些新變化，我可以先幫你整理成最新版品牌資料，之後產出的內容會更準。",
            NUDGE_SLEEPING_CUSTOMERS: "好久不見，最近店裡準備了新的內容，想邀請你有空再回來看看。",
        }
        category_map = {
            NUDGE_NO_POST: "brand_presence",
            NUDGE_NEGATIVE_REVIEW: "brand_presence",
            NUDGE_STALE_KNOWLEDGE: "knowledge_health",
            NUDGE_SLEEPING_CUSTOMERS: "customer_relationship",
        }
        affected_profiles: list[str] = []
        profile_count = 0
        if suggestion_type == NUDGE_SLEEPING_CUSTOMERS:
            tenant = self._repo.get_tenant(tenant_id)
            threshold = getattr(tenant, "sleep_threshold", 60) if tenant is not None else 60
            sleeping = self._repo.list_sleeping_customer_profiles(tenant_id, minimum_days=threshold, limit=20)
            affected_profiles = [profile.id for profile in sleeping]
            profile_count = len(affected_profiles)
        suggestion = self._repo.create_suggestion(
            tenant_id=tenant_id,
            suggestion_type=suggestion_type,
            category=category_map[suggestion_type],
            title=title_map[suggestion_type],
            reason=reason_map[suggestion_type],
            body=body_map[suggestion_type],
            affected_profile_ids=affected_profiles,
            profile_count=profile_count,
            suggested_action=action_map[suggestion_type],
            draft_message=draft_map[suggestion_type],
            payload={"detected_at": now.isoformat()},
            expires_at=now + timedelta(hours=24),
        )
        if self._settings is not None and self._settings.LINE_CHANNEL_ACCESS_TOKEN:
            recipients = resolve_tenant_line_recipients(repo=self._repo, settings=self._settings, tenant_id=tenant_id)
            if recipients:
                expires_label = suggestion.expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") if suggestion.expires_at else "24 小時內"
                async def _push_all() -> None:
                    for recipient in recipients:
                        await push_line_messages(
                            to=recipient,
                            messages=[
                                suggestion_card_message(
                                    suggestion_id=suggestion.id,
                                    title=title_map[suggestion_type],
                                    reason=reason_map[suggestion_type],
                                    suggested_action=action_map[suggestion_type],
                                    draft_message=draft_map[suggestion_type],
                                    profile_count=profile_count,
                                    expires_at=expires_label,
                                )
                            ],
                            access_token=self._settings.LINE_CHANNEL_ACCESS_TOKEN,
                        )

                try:
                    loop = asyncio.get_running_loop()
                    loop.create_task(_push_all())
                except RuntimeError:
                    asyncio.run(_push_all())
        return self._serialize_suggestion(suggestion)

    def run_once_all_tenants(self) -> dict[str, dict[str, Any]]:
        summary: dict[str, dict[str, Any]] = {}
        for tenant in self._repo.list_active_tenants():
            job = self._repo.claim_due_recurring_job(tenant_id=tenant.id, job_type=PROACTIVE_SCAN_JOB)
            if job is None:
                continue
            try:
                result = self.run_once_for_tenant(tenant.id)
                if result is not None:
                    summary[tenant.id] = result
                self._repo.mark_recurring_job_completed(
                    job_id=job.id,
                    interval_seconds=86400,
                    result={"suggestion_id": result.get("id") if result else ""},
                )
            except Exception as exc:
                self._repo.mark_recurring_job_failed(
                    job_id=job.id,
                    result={"error": str(exc)},
                )
                raise
        return summary

    def send_due_reminders_all_tenants(self) -> int:
        total = 0
        for tenant in self._repo.list_active_tenants():
            total += self.send_due_reminders_for_tenant(tenant.id)
        return total

    async def send_due_meta_reports_all_tenants(self) -> int:
        sent = 0
        for tenant in self._repo.list_active_tenants():
            job = self._repo.claim_due_recurring_job(tenant_id=tenant.id, job_type=META_INSIGHTS_REPORT_JOB)
            if job is None:
                continue
            try:
                result = await self.send_due_meta_report_for_tenant(tenant.id)
                self._repo.mark_recurring_job_completed(
                    job_id=job.id,
                    interval_seconds=86400,
                    result=result,
                )
                if result.get("status") == "sent":
                    sent += 1
            except Exception as exc:
                self._repo.mark_recurring_job_failed(
                    job_id=job.id,
                    retry_after_seconds=3600,
                    result={"error": str(exc)},
                )
                raise
        return sent

    async def send_due_meta_report_for_tenant(self, tenant_id: str) -> dict[str, Any]:
        if self._settings is None:
            return {"status": "skipped", "reason": "settings_missing", "recipient_count": 0}

        service = self._meta_service or MetaInsightsService(self._repo, self._settings)
        try:
            insights = service.fetch_insights(tenant_id=tenant_id, period="week")
        except MetaConnectorError as exc:
            return {"status": "skipped", "reason": str(exc), "recipient_count": 0}

        tenant = self._repo.get_tenant(tenant_id)
        summary_payload = await summarize_meta_insights(
            tenant_name=getattr(tenant, "name", ""),
            industry_type=getattr(tenant, "industry_type", ""),
            insights=insights,
            consultant=self._consultant,
        )
        delivery = await deliver_meta_insights_report(
            repo=self._repo,
            settings=self._settings,
            tenant_id=tenant_id,
            summary=summary_payload["summary"],
            details=summary_payload["details"],
            period=str(insights.get("period", "week") or "week"),
        )
        return {
            **delivery,
            "period": insights.get("period", "week"),
            "detail_count": len(summary_payload["details"]),
        }

    def send_due_reminders_for_tenant(self, tenant_id: str) -> int:
        self._repo.expire_due_suggestions(tenant_id)
        if self._settings is None or not self._settings.LINE_CHANNEL_ACCESS_TOKEN:
            return 0
        recipients = resolve_tenant_line_recipients(repo=self._repo, settings=self._settings, tenant_id=tenant_id)
        if not recipients:
            return 0

        now = datetime.now(timezone.utc)
        reminders_sent = 0
        for suggestion in self._repo.list_pending_suggestions(tenant_id):
            expires_at = suggestion.expires_at
            if expires_at is None:
                continue
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if now < expires_at - timedelta(hours=12):
                continue
            snapshot = json.loads(suggestion.result_snapshot_json or "{}")
            if snapshot.get("reminded_at"):
                continue
            expires_label = expires_at.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            async def _push_all() -> None:
                for recipient in recipients:
                    await push_line_messages(
                        to=recipient,
                        messages=[
                            suggestion_card_message(
                                suggestion_id=suggestion.id,
                                title=f"提醒：{suggestion.title}",
                                reason=suggestion.reason,
                                suggested_action=suggestion.suggested_action,
                                draft_message=suggestion.draft_message,
                                profile_count=suggestion.profile_count,
                                expires_at=expires_label,
                            )
                        ],
                        access_token=self._settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )

            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_push_all())
            except RuntimeError:
                asyncio.run(_push_all())

            snapshot["reminded_at"] = now.isoformat()
            snapshot["reminder_count"] = 1
            self._repo.update_suggestion_status(
                suggestion_id=suggestion.id,
                status=suggestion.status,
                result_snapshot=snapshot,
            )
            reminders_sent += 1
        return reminders_sent

    def _serialize_suggestion(self, suggestion) -> dict[str, Any]:
        return {
            "id": suggestion.id,
            "suggestion_type": suggestion.suggestion_type,
            "category": suggestion.category,
            "title": suggestion.title,
            "reason": suggestion.reason,
            "body": suggestion.body,
            "profile_count": suggestion.profile_count,
            "suggested_action": suggestion.suggested_action,
            "draft_message": suggestion.draft_message,
            "status": suggestion.status,
            "expires_at": suggestion.expires_at.isoformat() if suggestion.expires_at is not None else None,
        }


class ProactiveSuggestionScheduler:
    def __init__(self, engine: ProactiveSuggestionEngine, *, interval_seconds: int = 300) -> None:
        self._engine = engine
        self._interval_seconds = max(int(interval_seconds), 300)
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run_loop())

    async def shutdown(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def run_once(self) -> dict[str, dict[str, Any]]:
        await self._engine.send_due_meta_reports_all_tenants()
        self._engine.send_due_reminders_all_tenants()
        return self._engine.run_once_all_tenants()

    async def _run_loop(self) -> None:
        await self.run_once()
        while True:
            await asyncio.sleep(self._interval_seconds)
            await self.run_once()