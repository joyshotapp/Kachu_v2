"""
Kachu scheduler — APScheduler-based background jobs.

Jobs:
  - ga4_weekly_report       : every Monday 08:00 Asia/Taipei → kachu_ga4_report
  - google_weekly_post      : every Thursday 10:00 Asia/Taipei → kachu_google_post
  - proactive_daily_scan    : daily 07:00 Asia/Taipei (Phase 5)
  - monthly_content_calendar: 1st of each month 09:00 Asia/Taipei (Phase 5)

Usage (called from main.py lifespan):
    scheduler = KachuScheduler(agentOS_client, repository, settings, memory)
    scheduler.start()
    ...
    scheduler.shutdown()
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

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
        # GA4 weekly report: every Monday 08:00 Asia/Taipei
        self._scheduler.add_job(
            self._trigger_ga4_reports,
            CronTrigger(day_of_week="mon", hour=8, minute=0, timezone="Asia/Taipei"),
            id="ga4_weekly_report",
            replace_existing=True,
            name="GA4 Weekly Report",
        )
        # Google post: every Thursday 10:00 Asia/Taipei
        self._scheduler.add_job(
            self._trigger_google_posts,
            CronTrigger(day_of_week="thu", hour=10, minute=0, timezone="Asia/Taipei"),
            id="google_weekly_post",
            replace_existing=True,
            name="Google Weekly Post",
        )
        # Phase 5: Proactive monitor — daily 07:00
        self._scheduler.add_job(
            self._run_proactive_scan,
            CronTrigger(hour=7, minute=0, timezone="Asia/Taipei"),
            id="proactive_daily_scan",
            replace_existing=True,
            name="Proactive Daily Scan",
        )
        # Phase 5: Content calendar — 1st of each month 09:00
        self._scheduler.add_job(
            self._run_content_calendar,
            CronTrigger(day=1, hour=9, minute=0, timezone="Asia/Taipei"),
            id="monthly_content_calendar",
            replace_existing=True,
            name="Monthly Content Calendar",
        )

    def start(self) -> None:
        self._scheduler.start()
        logger.info(
            "KachuScheduler started "
            "(ga4 Mon 08:00, google_post Thu 10:00, "
            "proactive daily 07:00, calendar 1st 09:00)"
        )

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("KachuScheduler shut down")

    # ── Job handlers ──────────────────────────────────────────────────────────

    async def _trigger_ga4_reports(self) -> None:
        """Trigger kachu_ga4_report for every active tenant."""
        tenant_ids = self._repo.list_active_tenant_ids()
        if not tenant_ids:
            logger.info("GA4 weekly report: no active tenants, skipping")
            return

        week_start = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for tenant_id in tenant_ids:
            idempotency_key = f"ga4_report:{tenant_id}:{week_start}"
            try:
                task_view = await self._agentOS.create_task(AgentOSTaskRequest(
                    tenant_id=tenant_id,
                    domain="kachu_ga4_report",
                    objective="Weekly GA4 report",
                    workflow_input={
                        "tenant_id": tenant_id,
                        "period": "7daysAgo",
                        "trigger_source": "schedule",
                    },
                    idempotency_key=idempotency_key,
                ))
                await self._agentOS.run_task(task_view.task["id"])
                logger.info("GA4 report triggered: tenant=%s task=%s", tenant_id, task_view.task["id"])
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
            idempotency_key = f"google_post:{tenant_id}:{trigger_date}:schedule"
            try:
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
                    idempotency_key=idempotency_key,
                ))
                await self._agentOS.run_task(task_view.task["id"])
                logger.info("Google post triggered: tenant=%s task=%s", tenant_id, task_view.task["id"])
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
