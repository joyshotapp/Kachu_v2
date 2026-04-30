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
    ) -> None:
        workflow_input = {
            "tenant_id": tenant_id,
            "trigger_source": "schedule",
            "trigger_date": trigger_date,
        }
        if self._policy_resolver is not None:
            workflow_input.update(self._policy_resolver.resolve(tenant_id).to_workflow_input_patch())
        task_view = await self._agentOS.create_task(AgentOSTaskRequest(
            tenant_id=tenant_id,
            domain="kachu_google_post",
            objective="Weekly Google Business post",
            workflow_input=workflow_input,
            idempotency_key=f"google_post:{tenant_id}:{schedule_bucket}:schedule",
        ))
        await self._agentOS.run_task(task_view.task["id"])
        logger.info("Google post triggered: tenant=%s task=%s", tenant_id, task_view.task["id"])

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
                    )
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.error("Configured Google post failed for tenant=%s: %s", tenant_id, exc)

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
