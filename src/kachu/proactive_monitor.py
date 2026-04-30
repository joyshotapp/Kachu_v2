"""
Phase 5: ProactiveMonitorAgent

Runs on the scheduler's configured cadence. Scans each active tenant for
situations that warrant an unprompted nudge to the boss while suppressing
duplicates for the same tenant, nudge type, and schedule bucket.

Detection rules:
  1. No content published in the last 7 days
  2. Pending negative review reply older than 1 hour
  3. Knowledge base last updated > 60 days ago
  4. (Future) GA4 traffic drop > 20% — requires SharedContext ga4_recommendations

Each condition results in a direct LINE push when allowed by rate limits and
when the same nudge has not already been sent in the same bucket.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy.exc import SQLAlchemyError

from .agentOS_client import AgentOSClient
from .models import AgentOSTaskRequest
from .persistence import KachuRepository
from .line.push import push_line_messages, text_message

logger = logging.getLogger(__name__)

_NUDGE_DOMAIN = "kachu_proactive_nudge"

# Nudge type constants
NUDGE_NO_POST = "no_recent_post"
NUDGE_NEGATIVE_REVIEW = "pending_negative_review"
NUDGE_STALE_KNOWLEDGE = "stale_knowledge_base"

_NUDGE_MESSAGES = {
    NUDGE_NO_POST: "提醒：最近 7 天還沒有新的對外發文，要不要我先幫你準備一篇本週貼文？",
    NUDGE_NEGATIVE_REVIEW: "提醒：有待處理的顧客評論還沒回覆，我可以先幫你起草回覆。",
    NUDGE_STALE_KNOWLEDGE: "提醒：知識庫已經超過 60 天沒更新，建議補一下近期菜單或活動資訊。",
}

class ProactiveMonitorAgent:
    """
    Scans all active tenants daily and sends a boss nudge when
    one of the proactive conditions is met.

    The AgentOS `kachu_proactive_nudge` workflow (a single-step fire-and-forget
    plan) calls Kachu's `/tools/send-proactive-nudge` endpoint which pushes a
    targeted LINE message to the boss.
    """

    def __init__(
        self,
        agentOS_client: AgentOSClient,
        repo: KachuRepository,
        settings,
    ) -> None:
        self._agentOS = agentOS_client
        self._repo = repo
        self._settings = settings

    async def scan_and_nudge(self) -> None:
        """Main entry point — called by scheduler daily."""
        tenant_ids = self._repo.list_active_tenant_ids()
        if not tenant_ids:
            logger.info("ProactiveMonitor: no active tenants, skipping")
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        triggered = 0

        for tenant_id in tenant_ids:
            try:
                nudge_type = self._detect_nudge(tenant_id)
                if nudge_type:
                    await self._trigger_nudge(tenant_id, nudge_type, today)
                    triggered += 1
            except SQLAlchemyError as exc:
                logger.error("ProactiveMonitor scan failed for tenant=%s: %s", tenant_id, exc)

        logger.info("ProactiveMonitor: scanned %d tenants, triggered %d nudges", len(tenant_ids), triggered)

    async def scan_tenant_and_nudge(self, tenant_id: str, bucket: str) -> bool:
        """Run proactive checks for one tenant when its configured cadence is due."""
        try:
            nudge_type = self._detect_nudge(tenant_id)
            if not nudge_type:
                return False
            await self._trigger_nudge(tenant_id, nudge_type, bucket)
            return True
        except SQLAlchemyError as exc:
            logger.error("ProactiveMonitor scan failed for tenant=%s: %s", tenant_id, exc)
            return False

    def _detect_nudge(self, tenant_id: str) -> str | None:
        """Return the first applicable nudge type for this tenant, or None."""
        now = datetime.now(timezone.utc)

        # Rule 1: No content published in 7 days
        last_published = self._repo.get_last_published_at(tenant_id)
        if last_published is None or (now - last_published) > timedelta(days=7):
            return NUDGE_NO_POST

        # Rule 2: Pending negative review reply older than 1 hour
        if self._repo.get_pending_negative_reviews(tenant_id) > 0:
            return NUDGE_NEGATIVE_REVIEW

        # Rule 3: Knowledge base stale > 60 days
        kb_updated = self._repo.get_knowledge_last_updated_at(tenant_id)
        if kb_updated and (now - kb_updated) > timedelta(days=60):
            return NUDGE_STALE_KNOWLEDGE

        return None

    async def _trigger_nudge(self, tenant_id: str, nudge_type: str, today: str) -> None:
        """Send a direct LINE nudge to the boss (idempotent enough for daily scan cadence)."""
        if not self._settings.LINE_BOSS_USER_ID or not self._settings.LINE_CHANNEL_ACCESS_TOKEN:
            logger.info("ProactiveMonitor: LINE not configured, skipping tenant=%s type=%s", tenant_id, nudge_type)
            return

        dedupe_payload = {
            "message_type": "general",
            "nudge_type": nudge_type,
            "bucket": today,
        }
        if self._repo.has_recent_audit_event(
            tenant_id=tenant_id,
            workflow_type="proactive_monitor",
            event_type="push_sent",
            source="proactive_monitor",
            since=datetime.now(timezone.utc) - timedelta(days=7),
            payload_subset=dedupe_payload,
        ):
            logger.info(
                "ProactiveMonitor: duplicate nudge skipped tenant=%s type=%s bucket=%s",
                tenant_id,
                nudge_type,
                today,
            )
            return

        max_push = getattr(self._settings, "MAX_PUSH_PER_DAY", 3)
        if not self._repo.can_push(tenant_id, max_per_day=max_push):
            logger.info("ProactiveMonitor: push limited tenant=%s type=%s", tenant_id, nudge_type)
            return

        message = text_message(_NUDGE_MESSAGES.get(nudge_type, "提醒：我發現有一件事值得你注意。"))
        try:
            await push_line_messages(
                to=self._settings.LINE_BOSS_USER_ID,
                messages=[message],
                access_token=self._settings.LINE_CHANNEL_ACCESS_TOKEN,
            )
            self._repo.record_push(
                tenant_id=tenant_id,
                recipient_line_id=self._settings.LINE_BOSS_USER_ID,
                message_type="general",
            )
            self._repo.save_audit_event(
                tenant_id=tenant_id,
                workflow_type="proactive_monitor",
                event_type="push_sent",
                source="proactive_monitor",
                payload=dedupe_payload,
            )
            logger.info("ProactiveMonitor: nudge pushed tenant=%s type=%s day=%s", tenant_id, nudge_type, today)
        except httpx.HTTPError as exc:
            try:
                self._repo.save_audit_event(
                    tenant_id=tenant_id,
                    workflow_type="proactive_monitor",
                    event_type="push_failed",
                    source="proactive_monitor",
                    payload={"message_type": "general", "nudge_type": nudge_type, "error": str(exc)},
                )
            except SQLAlchemyError as audit_exc:
                logger.error(
                    "ProactiveMonitor push audit failed tenant=%s type=%s: %s",
                    tenant_id,
                    nudge_type,
                    audit_exc,
                )
            logger.error("ProactiveMonitor push failed tenant=%s type=%s: %s", tenant_id, nudge_type, exc)
        except SQLAlchemyError as exc:
            logger.error("ProactiveMonitor persistence failed tenant=%s type=%s: %s", tenant_id, nudge_type, exc)
