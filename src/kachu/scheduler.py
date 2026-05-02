"""
Kachu scheduler — APScheduler-based background jobs.

Jobs:
    - configured_automation_dispatch: every hour, evaluate each tenant's local schedule
    - deferred_dispatch_retry       : every 5 minutes, retry recoverable AgentOS dispatch failures

Usage (called from main.py lifespan):
    scheduler = KachuScheduler(agentOS_client, repository, settings, memory)
    scheduler.start()
    ...
    scheduler.shutdown()
"""
from __future__ import annotations

import logging
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError

from .agentOS_client import AgentOSClient
from .memory.manager import MemoryManager
from .models import AgentOSTaskRequest
from .persistence import KachuRepository
from .policy import KachuExecutionPolicyResolver

logger = logging.getLogger(__name__)

_WEEKDAY_INDEX = {
    "mon": 0,
    "tue": 1,
    "wed": 2,
    "thu": 3,
    "fri": 4,
    "sat": 5,
    "sun": 6,
}


class KachuScheduler:
    def __init__(
        self,
        agentOS_client: AgentOSClient,
        repository: KachuRepository,
        settings,
        memory: MemoryManager | None = None,
        policy_resolver: KachuExecutionPolicyResolver | None = None,
    ) -> None:
        self._agentOS = agentOS_client
        self._repo = repository
        self._settings = settings
        self._memory = memory
        self._policy_resolver = policy_resolver
        self._scheduler = AsyncIOScheduler(timezone="Asia/Taipei")
        self._register_jobs()

    def _register_jobs(self) -> None:
        self._scheduler.add_job(
            self._run_configured_automations,
            CronTrigger(minute=0, timezone="Asia/Taipei"),
            id="configured_automation_dispatch",
            replace_existing=True,
            name="Configured Automation Dispatch",
        )
        self._scheduler.add_job(
            self._drain_deferred_dispatches,
            CronTrigger(minute="*/5", timezone="Asia/Taipei"),
            id="deferred_dispatch_retry",
            replace_existing=True,
            name="Deferred Dispatch Retry",
        )
        # Flow B: post-performance check ~24h after publish (runs every hour)
        self._scheduler.add_job(
            self._scan_post_performance,
            CronTrigger(minute=0, timezone="Asia/Taipei"),
            id="post_performance_check",
            replace_existing=True,
            name="Post Performance Check (24h after publish)",
        )
        # Flow C: comment monitoring every 2 hours
        self._scheduler.add_job(
            self._scan_fb_comments,
            CronTrigger(hour="*/2", minute=10, timezone="Asia/Taipei"),
            id="fb_comment_scan",
            replace_existing=True,
            name="FB Comment Scan (every 2h)",
        )
        self._scheduler.add_job(
            self._dispatch_scheduled_publishes,
            CronTrigger(minute="*", timezone="Asia/Taipei"),
            id="line_scheduled_publish_dispatch",
            replace_existing=True,
            name="LINE Scheduled Publish Dispatch",
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info(
            "KachuScheduler started "
            "(configured automation dispatch every hour, deferred retry every 5m)"
        )

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("KachuScheduler shut down")

    # ── Job handlers ──────────────────────────────────────────────────────────

    def _tenant_now(self, tenant_id: str) -> datetime:
        tenant = self._repo.get_tenant(tenant_id) or self._repo.get_or_create_tenant(tenant_id)
        timezone_name = getattr(tenant, "timezone", "Asia/Taipei") or "Asia/Taipei"
        try:
            tzinfo = ZoneInfo(timezone_name)
        except Exception:
            tzinfo = ZoneInfo("Asia/Taipei")
        return datetime.now(tzinfo)

    def _normalize_hour(self, value: object, default: int) -> int:
        try:
            hour = int(value)
        except (TypeError, ValueError):
            return default
        return min(max(hour, 0), 23)

    def _normalize_day(self, value: object, default: int) -> int:
        try:
            day = int(value)
        except (TypeError, ValueError):
            return default
        return min(max(day, 1), 28)

    def _normalize_weekday(self, value: object, default: str) -> str:
        weekday = str(value or default).strip().lower()[:3]
        return weekday if weekday in _WEEKDAY_INDEX else default

    def _schedule_bucket(
        self,
        *,
        enabled: bool,
        frequency: str,
        hour: int,
        now_local: datetime,
        weekday: str | None = None,
        day: int | None = None,
    ) -> str | None:
        if not enabled:
            return None
        if now_local.hour != hour:
            return None

        normalized = str(frequency or "off").strip().lower()
        if normalized == "daily":
            return now_local.strftime("%Y-%m-%d")
        if normalized == "weekly":
            if weekday is None or now_local.weekday() != _WEEKDAY_INDEX[weekday]:
                return None
            return now_local.strftime("%Y-%m-%d")
        if normalized == "monthly":
            if day is None or now_local.day != day:
                return None
            return now_local.strftime("%Y-%m")
        return None

    async def _trigger_ga4_report_for_tenant(
        self,
        tenant_id: str,
        *,
        period: str = "7daysAgo",
        schedule_bucket: str,
    ) -> None:
        idempotency_key = f"ga4_report:{tenant_id}:{schedule_bucket}"
        task_view = await self._agentOS.create_task(AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_ga4_report",
            objective="Weekly GA4 report",
            workflow_input={
                "tenant_id": tenant_id,
                "period": period,
                "trigger_source": "schedule",
            },
            idempotency_key=idempotency_key,
        ))
        await self._agentOS.run_task(task_view.task["id"])
        logger.info("GA4 report triggered: tenant=%s task=%s", tenant_id, task_view.task["id"])

    async def _trigger_google_post_for_tenant(
        self,
        tenant_id: str,
        *,
        trigger_date: str,
        schedule_bucket: str,
        selected_platforms: list[str] | None = None,
        objective: str = "Weekly Google Business post",
        idempotency_prefix: str = "google_post",
    ) -> None:
        platforms = selected_platforms or ["google"]
        workflow_input = {
            "tenant_id": tenant_id,
            "trigger_source": "schedule",
            "trigger_date": trigger_date,
            "selected_platforms": platforms,
        }
        if self._policy_resolver is not None:
            workflow_input.update(self._policy_resolver.resolve(tenant_id).to_workflow_input_patch())
        task_view = await self._agentOS.create_task(AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_google_post",
            objective=objective,
            workflow_input=workflow_input,
            idempotency_key=f"{idempotency_prefix}:{tenant_id}:{schedule_bucket}:schedule",
        ))
        await self._agentOS.run_task(task_view.task["id"])
        logger.info("Scheduled post triggered: tenant=%s platforms=%s task=%s", tenant_id, platforms, task_view.task["id"])

    async def _run_configured_automations(self) -> None:
        from .content_calendar import ContentCalendarAgent
        from .proactive_monitor import ProactiveMonitorAgent

        tenant_ids = self._repo.list_active_tenant_ids()
        if not tenant_ids:
            logger.info("Configured automations: no active tenants, skipping")
            return

        proactive_agent = ProactiveMonitorAgent(self._agentOS, self._repo, self._settings)
        calendar_agent = ContentCalendarAgent(self._repo, self._memory, self._settings) if self._memory else None

        for tenant_id in tenant_ids:
            settings_row = self._repo.get_or_create_automation_settings(tenant_id)
            now_local = self._tenant_now(tenant_id)

            ga_bucket = self._schedule_bucket(
                enabled=bool(settings_row.ga_report_enabled),
                frequency=settings_row.ga_report_frequency,
                hour=self._normalize_hour(settings_row.ga_report_hour, 8),
                weekday=self._normalize_weekday(settings_row.ga_report_weekday, "mon"),
                now_local=now_local,
            )
            if ga_bucket:
                try:
                    await self._trigger_ga4_report_for_tenant(
                        tenant_id,
                        period="7daysAgo",
                        schedule_bucket=ga_bucket,
                    )
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.error("Configured GA4 report failed for tenant=%s: %s", tenant_id, exc)

            google_bucket = self._schedule_bucket(
                enabled=bool(settings_row.google_post_enabled),
                frequency=settings_row.google_post_frequency,
                hour=self._normalize_hour(settings_row.google_post_hour, 10),
                weekday=self._normalize_weekday(settings_row.google_post_weekday, "thu"),
                now_local=now_local,
            )
            if google_bucket:
                try:
                    await self._trigger_google_post_for_tenant(
                        tenant_id,
                        trigger_date=now_local.strftime("%Y-%m-%d"),
                        schedule_bucket=google_bucket,
                        selected_platforms=["google"],
                        objective="Weekly Google Business post",
                        idempotency_prefix="google_post",
                    )
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.error("Configured Google post failed for tenant=%s: %s", tenant_id, exc)

            meta_bucket = self._schedule_bucket(
                enabled=bool(settings_row.meta_post_enabled),
                frequency=settings_row.meta_post_frequency,
                hour=self._normalize_hour(settings_row.meta_post_hour, 11),
                weekday=self._normalize_weekday(settings_row.meta_post_weekday, "fri"),
                now_local=now_local,
            )
            if meta_bucket:
                try:
                    await self._trigger_google_post_for_tenant(
                        tenant_id,
                        trigger_date=now_local.strftime("%Y-%m-%d"),
                        schedule_bucket=meta_bucket,
                        selected_platforms=["ig_fb"],
                        objective="Weekly Meta scheduled post",
                        idempotency_prefix="meta_post",
                    )
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.error("Configured Meta post failed for tenant=%s: %s", tenant_id, exc)

            proactive_bucket = self._schedule_bucket(
                enabled=bool(settings_row.proactive_enabled),
                frequency="daily",
                hour=self._normalize_hour(settings_row.proactive_hour, 7),
                now_local=now_local,
            )
            if proactive_bucket:
                await proactive_agent.scan_tenant_and_nudge(tenant_id, proactive_bucket)

            if calendar_agent is not None:
                calendar_bucket = self._schedule_bucket(
                    enabled=bool(settings_row.content_calendar_enabled),
                    frequency="monthly",
                    hour=self._normalize_hour(settings_row.content_calendar_hour, 9),
                    day=self._normalize_day(settings_row.content_calendar_day, 1),
                    now_local=now_local,
                )
                if calendar_bucket:
                    try:
                        await calendar_agent.generate_and_save(tenant_id)
                    except (httpx.HTTPError, SQLAlchemyError, ValidationError) as exc:
                        logger.error("Configured content calendar failed tenant=%s: %s", tenant_id, exc)

    async def _trigger_ga4_reports(self) -> None:
        """Trigger kachu_ga4_report for every active tenant."""
        tenant_ids = self._repo.list_active_tenant_ids()
        if not tenant_ids:
            logger.info("GA4 weekly report: no active tenants, skipping")
            return

        week_start = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for tenant_id in tenant_ids:
            try:
                await self._trigger_ga4_report_for_tenant(
                    tenant_id,
                    period="7daysAgo",
                    schedule_bucket=week_start,
                )
            except (httpx.HTTPError, ValidationError) as exc:
                logger.error("GA4 report failed for tenant=%s: %s", tenant_id, exc)

    async def _trigger_google_posts(self) -> None:
        """Trigger kachu_google_post for every active tenant."""
        tenant_ids = self._repo.list_active_tenant_ids()
        if not tenant_ids:
            logger.info("Google weekly post: no active tenants, skipping")
            return

        trigger_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for tenant_id in tenant_ids:
            try:
                await self._trigger_google_post_for_tenant(
                    tenant_id,
                    trigger_date=trigger_date,
                    schedule_bucket=trigger_date,
                    selected_platforms=["google"],
                    objective="Weekly Google Business post",
                    idempotency_prefix="google_post",
                )
            except (httpx.HTTPError, ValidationError) as exc:
                logger.error("Google post failed for tenant=%s: %s", tenant_id, exc)

    async def _run_proactive_scan(self) -> None:
        """Phase 5: daily proactive monitoring scan."""
        from .proactive_monitor import ProactiveMonitorAgent
        agent = ProactiveMonitorAgent(self._agentOS, self._repo, self._settings)
        try:
            await agent.scan_and_nudge()
        except (httpx.HTTPError, SQLAlchemyError, ValidationError) as exc:
            logger.error("Proactive daily scan failed: %s", exc)

    async def _run_content_calendar(self) -> None:
        """Phase 5: monthly content calendar generation for all tenants."""
        if self._memory is None:
            logger.warning("ContentCalendar: MemoryManager not available, skipping")
            return
        from .content_calendar import ContentCalendarAgent
        agent = ContentCalendarAgent(self._repo, self._memory, self._settings)
        try:
            await agent.scan_all_tenants()
        except (httpx.HTTPError, SQLAlchemyError, ValidationError) as exc:
            logger.error("Monthly content calendar failed: %s", exc)

    async def _drain_deferred_dispatches(self) -> None:
        """Retry recoverable AgentOS dispatch failures from the deferred queue."""
        deferred_items = self._repo.list_due_deferred_dispatches(limit=20)
        for item in deferred_items:
            try:
                task_request = AgentOSTaskRequest.model_validate(json.loads(item.task_request_json))
                trigger_payload = json.loads(item.trigger_payload or "{}")
                task_view = await self._agentOS.create_task(task_request)
                task_id = task_view.task["id"]
                run_view = await self._agentOS.run_task(task_id)
                run_id = run_view.run["id"]
                self._repo.create_workflow_record(
                    tenant_id=item.tenant_id,
                    agentos_run_id=run_id,
                    agentos_task_id=task_id,
                    workflow_type=item.workflow_type,
                    trigger_source=item.trigger_source,
                    trigger_payload=trigger_payload,
                )
                self._repo.mark_deferred_dispatch_dispatched(item.id)
                self._repo.save_audit_event(
                    tenant_id=item.tenant_id,
                    agentos_run_id=run_id,
                    agentos_task_id=task_id,
                    workflow_type=item.workflow_type,
                    event_type="dispatch_recovered",
                    source="scheduler",
                    payload={"deferred_dispatch_id": item.id},
                )
            except (httpx.HTTPError, ValidationError, SQLAlchemyError, json.JSONDecodeError) as exc:
                logger.error("Deferred dispatch retry failed id=%s: %s", item.id, exc)
                self._repo.mark_deferred_dispatch_retry(item.id, str(exc))

    async def _dispatch_scheduled_publishes(self) -> None:
        """Every minute: publish due LINE-confirmed scheduled posts."""
        due_items = self._repo.list_due_scheduled_publishes(limit=20)
        if not due_items:
            return

        base_url = getattr(self._settings, "KACHU_BASE_URL", "http://localhost:8000")
        api_key = getattr(self._settings, "KACHU_INTERNAL_API_KEY", "") or getattr(self._settings, "AGENTOS_API_KEY", "")
        headers = {"X-API-Key": api_key} if api_key else {}

        async with httpx.AsyncClient(timeout=30) as client:
            for item in due_items:
                self._repo.update_scheduled_publish_status(item.id, status="publishing")
                try:
                    draft_content = json.loads(item.draft_content or "{}")
                    selected_platforms = json.loads(item.selected_platforms or "[]")
                    endpoint, payload = self._build_scheduled_publish_request(item, draft_content, selected_platforms)
                    response = await client.post(f"{base_url}{endpoint}", json=payload, headers=headers)
                    response.raise_for_status()
                    result = response.json()
                    published, error_message = self._interpret_scheduled_publish_result(item.workflow_type, result)
                    if published:
                        self._repo.update_scheduled_publish_status(
                            item.id,
                            status="published",
                            error_message=error_message,
                            published_at=datetime.now(timezone.utc),
                        )
                        self._repo.save_audit_event(
                            tenant_id=item.tenant_id,
                            agentos_run_id=item.source_run_id,
                            workflow_type=item.workflow_type,
                            event_type="scheduled_publish_completed",
                            source="scheduler",
                            payload={"scheduled_publish_id": item.id, "result": result},
                        )
                    else:
                        self._repo.update_scheduled_publish_status(
                            item.id,
                            status="failed",
                            error_message=error_message or json.dumps(result, ensure_ascii=False),
                        )
                        self._repo.save_audit_event(
                            tenant_id=item.tenant_id,
                            agentos_run_id=item.source_run_id,
                            workflow_type=item.workflow_type,
                            event_type="scheduled_publish_failed",
                            source="scheduler",
                            payload={"scheduled_publish_id": item.id, "result": result},
                        )
                except (httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                    logger.error("Scheduled publish failed id=%s: %s", item.id, exc)
                    self._repo.update_scheduled_publish_status(
                        item.id,
                        status="failed",
                        error_message=str(exc),
                    )

    def _build_scheduled_publish_request(
        self,
        item,
        draft_content: dict[str, object],
        selected_platforms: list[str],
    ) -> tuple[str, dict[str, object]]:
        if item.workflow_type in ("kachu_google_post", "google_post"):
            post_text = (
                str(draft_content.get("post_text", ""))
                or str(draft_content.get("google", ""))
                or str(draft_content.get("ig_fb", ""))
            )
            return "/tools/publish-google-post", {
                "tenant_id": item.tenant_id,
                "run_id": item.source_run_id,
                "post_text": post_text,
                "selected_platforms": selected_platforms or ["google"],
                "drafts": draft_content,
            }

        return "/tools/publish-content", {
            "tenant_id": item.tenant_id,
            "run_id": item.source_run_id,
            "selected_platforms": selected_platforms or ["ig_fb", "google"],
            "drafts": draft_content,
        }

    def _interpret_scheduled_publish_result(
        self,
        workflow_type: str,
        result: dict[str, object],
    ) -> tuple[bool, str | None]:
        if workflow_type in ("kachu_google_post", "google_post"):
            status = str(result.get("status", ""))
            if status == "published":
                return True, None
            return False, str(result.get("error") or result.get("reason") or "scheduled publish did not complete")

        platform_results = [value for value in result.values() if isinstance(value, dict)]
        published = any(str(value.get("status", "")) == "published" for value in platform_results)
        if published:
            failed = [value.get("error") or value.get("reason") for value in platform_results if str(value.get("status", "")) == "failed"]
            error_message = "; ".join(str(message) for message in failed if message) or None
            return True, error_message

        failed = [value.get("error") or value.get("reason") for value in platform_results if value.get("status")]
        error_message = "; ".join(str(message) for message in failed if message)
        return False, error_message or "scheduled publish did not complete"

    # ── Flow B: Post performance report ───────────────────────────────────────

    async def _scan_post_performance(self) -> None:
        """Every hour: push post-performance reports for runs that are ~24h old."""
        if not getattr(self._settings, "LINE_BOSS_USER_ID", ""):
            return
        base_url = getattr(self._settings, "KACHU_BASE_URL", "http://localhost:8000")
        api_key = getattr(self._settings, "KACHU_INTERNAL_API_KEY", "") or getattr(self._settings, "AGENTOS_API_KEY", "")
        headers = {"X-API-Key": api_key} if api_key else {}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{base_url}/tools/send-post-performance-report",
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                logger.info("Post performance scan done: sent=%s", data.get("sent", 0))
        except httpx.HTTPError as exc:
            logger.error("Post performance scan HTTP error: %s", exc)
        except (SQLAlchemyError, Exception) as exc:
            logger.error("Post performance scan failed: %s", exc)

    # ── Flow C: FB Comment monitoring ─────────────────────────────────────────

    async def _scan_fb_comments(self) -> None:
        """Every 2h: poll FB comments on recent posts, generate LLM reply drafts, push LINE Flex."""
        from datetime import timedelta

        settings = self._settings
        if not getattr(settings, "LINE_BOSS_USER_ID", "") or not getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", ""):
            return

        tenant_ids = self._repo.list_active_tenant_ids()
        for tenant_id in tenant_ids:
            try:
                await self._process_tenant_comments(tenant_id)
            except (SQLAlchemyError, Exception) as exc:
                logger.error("Comment scan failed for tenant=%s: %s", tenant_id, exc)

    async def _process_tenant_comments(self, tenant_id: str) -> None:
        """Scan recent FB posts for new comments and push notification Flex messages."""
        from datetime import timedelta

        from .line.flex_builder import build_comment_notify_flex
        from .line.push import push_line_messages
        from .meta import MetaAPIError
        from .meta.client import MetaClient

        settings = self._settings

        # Get Meta credentials
        creds_row = self._repo.get_connector_account(tenant_id, "meta")
        if not creds_row:
            return
        try:
            creds = json.loads(creds_row.credentials_json or "{}")
        except json.JSONDecodeError:
            return
        if not creds.get("fb_access_token") or not creds.get("fb_page_id"):
            return

        meta = MetaClient(
            fb_page_id=creds["fb_page_id"],
            fb_access_token=creds["fb_access_token"],
        )

        # Get recent trackable posts (last 7 days with fb_post_id)
        trackable = self._repo.list_comment_trackable_runs(tenant_id, within_days=7)
        if not trackable:
            return

        processed_any = False
        for fb_post_id, _run_id in trackable[:3]:  # limit to 3 most recent posts
            try:
                comments_data = await meta.get_fb_comments(object_id=fb_post_id, limit=10)
                comments = comments_data.get("data", [])
            except MetaAPIError as exc:
                logger.warning("get_fb_comments failed post=%s: %s", fb_post_id, exc)
                continue

            for comment in comments:
                comment_id = comment.get("id", "")
                comment_text = comment.get("message", "")
                comment_author = comment.get("from", {}).get("name", "訪客")
                if not comment_id or not comment_text:
                    continue

                # Dedup: skip already-notified comments
                already_notified = self._repo.has_recent_audit_event(
                    tenant_id=tenant_id,
                    workflow_type="comment_monitor",
                    event_type="comment_notified",
                    source="comment_scheduler",
                    since=datetime.now(timezone.utc) - timedelta(days=7),
                    payload_subset={"comment_id": comment_id},
                )
                if already_notified:
                    continue

                # Generate reply draft via LLM
                reply_draft = await self._generate_comment_reply_draft(
                    tenant_id=tenant_id,
                    comment_text=comment_text,
                )

                # Save draft to shared_context for later retrieval on postback
                self._repo.save_shared_context(
                    tenant_id=tenant_id,
                    context_type=f"comment_draft:{comment_id}",
                    content={"draft": reply_draft, "fb_post_id": fb_post_id},
                    ttl_hours=72,
                )

                # Push LINE Flex
                flex_msg = build_comment_notify_flex(
                    tenant_id=tenant_id,
                    comment_id=comment_id,
                    comment_author=comment_author,
                    comment_text=comment_text,
                    reply_draft=reply_draft,
                    platform="fb",
                    object_id=fb_post_id,
                )
                try:
                    await push_line_messages(
                        to=settings.LINE_BOSS_USER_ID,
                        messages=[{"type": "flex", "altText": f"💬 新留言：{comment_text[:30]}", "contents": flex_msg}],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                    self._repo.record_push(
                        tenant_id=tenant_id,
                        recipient_line_id=settings.LINE_BOSS_USER_ID,
                        message_type="comment_notify",
                    )
                    self._repo.save_audit_event(
                        tenant_id=tenant_id,
                        workflow_type="comment_monitor",
                        event_type="comment_notified",
                        source="comment_scheduler",
                        payload={"comment_id": comment_id, "fb_post_id": fb_post_id},
                    )
                    processed_any = True
                except httpx.HTTPError as exc:
                    logger.error("push comment notify failed tenant=%s comment=%s: %s", tenant_id, comment_id, exc)

        if processed_any:
            logger.info("Comment scan: notified new comments for tenant=%s", tenant_id)

    async def _generate_comment_reply_draft(self, tenant_id: str, comment_text: str) -> str:
        """Use LLM to generate a polite reply draft for a FB comment."""
        settings = self._settings
        api_key = getattr(settings, "GOOGLE_AI_API_KEY", "")
        openai_key = getattr(settings, "OPENAI_API_KEY", "")
        model = getattr(settings, "LITELLM_MODEL", "gemini/gemini-2.5-flash")

        # Include business context if available
        kb_summary = ""
        try:
            kb_entries = self._repo.list_knowledge_entries(tenant_id, limit=3)
            if kb_entries:
                kb_summary = "；".join(e.content[:80] for e in kb_entries[:3] if e.content)
        except Exception:
            pass

        from .llm import generate_text
        try:
            prompt = (
                f"一位顧客在 Facebook 留言說：「{comment_text}」\n"
                f"{'店家背景：' + kb_summary if kb_summary else ''}\n"
                "請用繁體中文以商家名義回覆這則留言，語氣親切專業，不超過 50 字。\n"
                "只輸出回覆內容，不加任何說明。"
            )
            draft = await generate_text(
                prompt=prompt,
                model=model,
                api_key=api_key,
                openai_api_key=openai_key,
            )
            return draft.strip()[:200]
        except Exception as exc:
            logger.warning("_generate_comment_reply_draft LLM failed: %s", exc)
            return "感謝您的留言！歡迎來電洽詢更多資訊。😊"
