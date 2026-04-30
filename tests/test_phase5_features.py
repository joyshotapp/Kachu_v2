"""
Tests for Phase 5:
- ProactiveMonitorAgent rule detection
- GoalParser domain classification (mocked LLM)
- SharedContext repository methods
- ContentCalendarAgent calendar generation (mocked LLM)
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy.exc import SQLAlchemyError

def _utcnow():
    return datetime.now(timezone.utc)


# ── ProactiveMonitorAgent ─────────────────────────────────────────────────────

class TestProactiveMonitor:
    def _make_agent(self, **repo_overrides):
        from kachu.proactive_monitor import ProactiveMonitorAgent
        repo = MagicMock()
        repo.list_active_tenant_ids.return_value = ["t1"]
        repo.get_last_published_at.return_value = _utcnow() - timedelta(days=3)
        repo.get_pending_negative_reviews.return_value = 0
        repo.get_knowledge_last_updated_at.return_value = _utcnow() - timedelta(days=30)
        repo.can_push.return_value = True
        repo.has_recent_audit_event.return_value = False
        repo.record_push = MagicMock()
        for k, v in repo_overrides.items():
            setattr(repo, k, MagicMock(return_value=v))
        agentOS = MagicMock()
        settings = MagicMock()
        settings.LINE_BOSS_USER_ID = "boss-1"
        settings.LINE_CHANNEL_ACCESS_TOKEN = "token-1"
        settings.MAX_PUSH_PER_DAY = 3
        return ProactiveMonitorAgent(agentOS, repo, settings), repo

    def test_no_nudge_when_recent_post(self):
        agent, repo = self._make_agent(
            get_last_published_at=_utcnow() - timedelta(days=1),
            get_pending_negative_reviews=0,
            get_knowledge_last_updated_at=_utcnow() - timedelta(days=10),
        )
        result = agent._detect_nudge("t1")
        assert result is None

    def test_nudge_no_post_7days(self):
        from kachu.proactive_monitor import NUDGE_NO_POST
        agent, repo = self._make_agent(
            get_last_published_at=_utcnow() - timedelta(days=8),
        )
        result = agent._detect_nudge("t1")
        assert result == NUDGE_NO_POST

    def test_nudge_no_post_when_never_published(self):
        from kachu.proactive_monitor import NUDGE_NO_POST
        agent, repo = self._make_agent(get_last_published_at=None)
        result = agent._detect_nudge("t1")
        assert result == NUDGE_NO_POST

    def test_nudge_negative_review(self):
        from kachu.proactive_monitor import NUDGE_NEGATIVE_REVIEW
        agent, repo = self._make_agent(
            get_last_published_at=_utcnow() - timedelta(days=1),
            get_pending_negative_reviews=2,
        )
        result = agent._detect_nudge("t1")
        assert result == NUDGE_NEGATIVE_REVIEW

    def test_nudge_stale_knowledge(self):
        from kachu.proactive_monitor import NUDGE_STALE_KNOWLEDGE
        agent, repo = self._make_agent(
            get_last_published_at=_utcnow() - timedelta(days=1),
            get_pending_negative_reviews=0,
            get_knowledge_last_updated_at=_utcnow() - timedelta(days=61),
        )
        result = agent._detect_nudge("t1")
        assert result == NUDGE_STALE_KNOWLEDGE

    @pytest.mark.asyncio
    async def test_scan_triggers_nudge(self):
        from kachu.proactive_monitor import NUDGE_NO_POST
        agent, repo = self._make_agent(
            get_last_published_at=_utcnow() - timedelta(days=10),
        )
        with patch("kachu.proactive_monitor.push_line_messages", new=AsyncMock()) as push_mock:
            await agent.scan_and_nudge()
        push_mock.assert_called_once()
        assert repo.record_push.called

    @pytest.mark.asyncio
    async def test_scan_no_tenants(self):
        agent, repo = self._make_agent()
        repo.list_active_tenant_ids.return_value = []
        await agent.scan_and_nudge()
        repo.record_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_continues_on_recoverable_db_error(self):
        agent, repo = self._make_agent()
        with patch.object(agent, "_detect_nudge", side_effect=SQLAlchemyError("db error")):
            await agent.scan_and_nudge()
        repo.record_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_re_raises_unexpected_error(self):
        agent, repo = self._make_agent()
        with patch.object(agent, "_detect_nudge", side_effect=AssertionError("unexpected")):
            with pytest.raises(AssertionError, match="unexpected"):
                await agent.scan_and_nudge()

    @pytest.mark.asyncio
    async def test_trigger_nudge_logs_recoverable_push_error(self):
        from kachu.proactive_monitor import NUDGE_NO_POST

        agent, repo = self._make_agent()
        with patch("kachu.proactive_monitor.push_line_messages", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
            await agent._trigger_nudge("t1", NUDGE_NO_POST, "2026-04-27")

        assert repo.save_audit_event.called
        repo.record_push.assert_not_called()

    @pytest.mark.asyncio
    async def test_trigger_nudge_re_raises_unexpected_push_error(self):
        from kachu.proactive_monitor import NUDGE_NO_POST

        agent, repo = self._make_agent()
        with patch("kachu.proactive_monitor.push_line_messages", new=AsyncMock(side_effect=AssertionError("unexpected"))):
            with pytest.raises(AssertionError, match="unexpected"):
                await agent._trigger_nudge("t1", NUDGE_NO_POST, "2026-04-27")

    @pytest.mark.asyncio
    async def test_trigger_nudge_skips_duplicate_bucket(self):
        from kachu.proactive_monitor import NUDGE_NO_POST

        agent, repo = self._make_agent(has_recent_audit_event=True)
        with patch("kachu.proactive_monitor.push_line_messages", new=AsyncMock()) as push_mock:
            await agent._trigger_nudge("t1", NUDGE_NO_POST, "2026-04-27")

        push_mock.assert_not_called()
        repo.record_push.assert_not_called()


# ── GoalParser ────────────────────────────────────────────────────────────────

class TestGoalParser:
    def _make_parser(self, llm_response: str = "traffic"):
        from kachu.goal_parser import GoalParser
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = "fake-key"
        settings.OPENAI_API_KEY = ""
        settings.LITELLM_MODEL = "gemini/test"
        parser = GoalParser(settings)
        return parser

    @pytest.mark.asyncio
    async def test_parse_returns_actions(self):
        from kachu.goal_parser import GoalParser, _DOMAIN_ACTIONS
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = ""
        settings.OPENAI_API_KEY = ""
        parser = GoalParser(settings)
        # Without API keys it uses DEFAULT_DOMAIN
        actions = await parser.parse("最近生意不好怎麼辦")
        assert len(actions) > 0
        assert "label" in actions[0]
        assert "intent" in actions[0]

    @pytest.mark.asyncio
    async def test_parse_with_mocked_llm(self):
        from kachu.goal_parser import GoalParser, _DOMAIN_ACTIONS
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = "fake"
        settings.OPENAI_API_KEY = ""
        settings.LITELLM_MODEL = "gemini/test"
        parser = GoalParser(settings)
        with patch("kachu.goal_parser.generate_text", new=AsyncMock(return_value="reputation")):
            actions = await parser.parse("最近有很多負評")
        assert all(a["intent"] in ("review_reply", "knowledge_update") for a in actions)

    @pytest.mark.asyncio
    async def test_parse_falls_back_on_recoverable_llm_error(self):
        from kachu.goal_parser import GoalParser, _DOMAIN_ACTIONS
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = "fake"
        settings.OPENAI_API_KEY = ""
        settings.LITELLM_MODEL = "gemini/test"
        parser = GoalParser(settings)
        with patch("kachu.goal_parser.generate_text", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
            actions = await parser.parse("最近有很多負評")
        assert actions == _DOMAIN_ACTIONS["content"]

    @pytest.mark.asyncio
    async def test_parse_re_raises_unexpected_llm_error(self):
        from kachu.goal_parser import GoalParser
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = "fake"
        settings.OPENAI_API_KEY = ""
        settings.LITELLM_MODEL = "gemini/test"
        parser = GoalParser(settings)
        with patch("kachu.goal_parser.generate_text", new=AsyncMock(side_effect=AssertionError("unexpected"))):
            with pytest.raises(AssertionError, match="unexpected"):
                await parser.parse("最近有很多負評")

    def test_build_quick_reply_items(self):
        from kachu.goal_parser import GoalParser
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = ""
        settings.OPENAI_API_KEY = ""
        parser = GoalParser(settings)
        actions = [{"label": "查看流量報告", "intent": "ga4_report", "topic": ""}]
        qr = parser.build_line_quick_reply(actions)
        assert qr["type"] == "quickReply"
        assert len(qr["items"]) == 1
        assert qr["items"][0]["action"]["type"] == "postback"
        assert "workflow=kachu_ga4_report" in qr["items"][0]["action"]["data"]


class TestBusinessConsultant:
    @pytest.mark.asyncio
    async def test_build_reply_uses_brand_and_quick_reply(self):
        from kachu.business_consultant import BusinessConsultant

        repo = MagicMock()
        repo.get_or_create_tenant.return_value = SimpleNamespace(name="好吃小館", industry_type="餐廳")
        repo.get_knowledge_entries.return_value = [
            SimpleNamespace(category="product", content="雞腿飯是招牌品項"),
            SimpleNamespace(category="goal", content="想增加午餐時段來客"),
        ]
        def _shared_context(_tenant_id, context_type):
            if context_type == "ga4_recommendations":
                return {"recommendations": [{"title": "強化午餐優惠"}]}
            if context_type == "monthly_content_calendar":
                return {"weeks": [{"topic": "母親節套餐"}]}
            return {}

        repo.get_shared_context.side_effect = _shared_context
        memory = MagicMock()
        memory.get_recent_episodes.return_value = []
        settings = MagicMock()
        settings.GOOGLE_AI_API_KEY = ""
        settings.OPENAI_API_KEY = ""
        settings.LITELLM_MODEL = "gemini/test"

        consultant = BusinessConsultant(repo, memory, settings)
        reply = await consultant.build_reply(tenant_id="t1", message="最近生意有點慢怎麼辦")

        assert reply["type"] == "text"
        assert "餐飲" in reply["text"]
        assert "quickReply" in reply
        assert reply["quickReply"]["items"]


class TestDeferredDispatchRetry:
    @pytest.mark.asyncio
    async def test_scheduler_recovers_deferred_dispatch(self):
        from kachu.scheduler import KachuScheduler

        agentos = AsyncMock()
        agentos.create_task.return_value = SimpleNamespace(task={"id": "task-1"})
        agentos.run_task.return_value = SimpleNamespace(run={"id": "run-1", "status": "queued"})
        repo = MagicMock()
        repo.list_due_deferred_dispatches.return_value = [
            SimpleNamespace(
                id="dd-1",
                tenant_id="tenant-1",
                workflow_type="google_post",
                task_request_json=json.dumps(
                    {
                        "tenant_id": "tenant-1",
                        "domain": "kachu_google_post",
                        "objective": "post",
                        "workflow_input": {"tenant_id": "tenant-1"},
                    }
                ),
                trigger_source="boss_request",
                trigger_payload=json.dumps({"message": "寫一篇貼文"}),
            )
        ]
        scheduler = KachuScheduler(agentos, repo, settings=MagicMock())

        await scheduler._drain_deferred_dispatches()

        repo.create_workflow_record.assert_called_once()
        repo.mark_deferred_dispatch_dispatched.assert_called_once_with("dd-1")

    @pytest.mark.asyncio
    async def test_scheduler_marks_retry_on_recoverable_error(self):
        from kachu.scheduler import KachuScheduler

        agentos = AsyncMock()
        agentos.create_task.side_effect = httpx.ReadTimeout("timeout")
        repo = MagicMock()
        repo.list_due_deferred_dispatches.return_value = [
            SimpleNamespace(
                id="dd-1",
                tenant_id="tenant-1",
                workflow_type="google_post",
                task_request_json=json.dumps(
                    {
                        "tenant_id": "tenant-1",
                        "domain": "kachu_google_post",
                        "objective": "post",
                        "workflow_input": {"tenant_id": "tenant-1"},
                    }
                ),
                trigger_source="boss_request",
                trigger_payload=json.dumps({"message": "寫一篇貼文"}),
            )
        ]
        scheduler = KachuScheduler(agentos, repo, settings=MagicMock())

        await scheduler._drain_deferred_dispatches()

        repo.mark_deferred_dispatch_retry.assert_called_once()

    @pytest.mark.asyncio
    async def test_scheduler_runs_due_configured_automations(self):
        from kachu.scheduler import KachuScheduler

        agentos = AsyncMock()
        repo = MagicMock()
        repo.list_active_tenant_ids.return_value = ["tenant-A"]
        repo.get_tenant.return_value = SimpleNamespace(timezone="Asia/Taipei")
        repo.get_or_create_automation_settings.return_value = SimpleNamespace(
            ga_report_enabled=True,
            ga_report_frequency="weekly",
            ga_report_weekday="mon",
            ga_report_hour=8,
            google_post_enabled=True,
            google_post_frequency="daily",
            google_post_weekday="thu",
            google_post_hour=8,
            proactive_enabled=False,
            proactive_hour=7,
            content_calendar_enabled=False,
            content_calendar_day=1,
            content_calendar_hour=9,
        )
        scheduler = KachuScheduler(agentos, repo, settings=MagicMock())
        scheduler._tenant_now = MagicMock(return_value=datetime(2026, 4, 27, 8, tzinfo=timezone.utc))
        scheduler._trigger_ga4_report_for_tenant = AsyncMock()
        scheduler._trigger_google_post_for_tenant = AsyncMock()

        await scheduler._run_configured_automations()

        scheduler._trigger_ga4_report_for_tenant.assert_called_once()
        scheduler._trigger_google_post_for_tenant.assert_called_once()


# ── SharedContext repository ──────────────────────────────────────────────────

class TestSharedContext:
    def _make_repo(self):
        from sqlmodel import create_engine
        from kachu.persistence.tables import SQLModel
        from kachu.persistence.repository import KachuRepository
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        return KachuRepository(engine)

    def test_save_and_get(self):
        repo = self._make_repo()
        repo.save_shared_context(
            tenant_id="t1",
            context_type="ga4_recommendations",
            content={"recommendations": [{"title": "SEO改善", "priority": "high"}]},
            source_run_id="run-123",
        )
        result = repo.get_shared_context("t1", "ga4_recommendations")
        assert result is not None
        assert result["recommendations"][0]["title"] == "SEO改善"

    def test_upsert_replaces_old(self):
        repo = self._make_repo()
        repo.save_shared_context(
            tenant_id="t1", context_type="ga4_recommendations",
            content={"recommendations": ["old"]},
        )
        repo.save_shared_context(
            tenant_id="t1", context_type="ga4_recommendations",
            content={"recommendations": ["new"]},
        )
        result = repo.get_shared_context("t1", "ga4_recommendations")
        assert result["recommendations"] == ["new"]

    def test_expired_returns_none(self):
        from datetime import timedelta
        repo = self._make_repo()
        # Save with -1 hour TTL (already expired)
        repo.save_shared_context(
            tenant_id="t1", context_type="test",
            content={"x": 1},
            ttl_hours=-1,
        )
        result = repo.get_shared_context("t1", "test")
        assert result is None

    def test_missing_returns_none(self):
        repo = self._make_repo()
        assert repo.get_shared_context("nobody", "ga4_recommendations") is None

    def test_different_tenants_isolated(self):
        repo = self._make_repo()
        repo.save_shared_context(
            tenant_id="t1", context_type="ga4_recommendations",
            content={"for": "t1"},
        )
        result_t2 = repo.get_shared_context("t2", "ga4_recommendations")
        assert result_t2 is None
        result_t1 = repo.get_shared_context("t1", "ga4_recommendations")
        assert result_t1["for"] == "t1"


class TestContextBriefManager:
    def _make_repo(self):
        from sqlmodel import create_engine
        from kachu.persistence.tables import SQLModel
        from kachu.persistence.repository import KachuRepository

        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        return KachuRepository(engine)

    @pytest.mark.asyncio
    async def test_refresh_briefs_persists_owner_and_brand_context(self):
        from kachu.context_brief_manager import ContextBriefManager

        repo = self._make_repo()
        tenant = repo.get_or_create_tenant("t1")
        tenant.name = "好吃小館"
        tenant.industry_type = "餐廳"
        repo.save_tenant(tenant)
        repo.save_knowledge_entry(tenant_id="t1", category="product", content="雞腿飯是招牌")
        repo.save_knowledge_entry(tenant_id="t1", category="goal", content="這週先衝午餐客")
        repo.save_knowledge_entry(tenant_id="t1", category="style", content="口吻要自然直接")
        repo.save_conversation(
            tenant_id="t1",
            role="owner",
            content="這週先衝午餐客，文案不要太空泛",
            conversation_type="general",
        )

        memory = MagicMock()
        memory.get_preference_examples.side_effect = [
            [{"notes": "老闆調整了用詞", "edited": "午餐方案要更直接"}],
            [],
        ]
        memory.get_recent_episodes.return_value = [{"outcome": "modified", "workflow_type": "google_post"}]

        manager = ContextBriefManager(repo, memory)
        briefs = await manager.refresh_briefs("t1", reason="test")

        assert "雞腿飯是招牌" in briefs["brand_brief"]["products"]
        assert briefs["owner_brief"]["current_priorities"][0].startswith("這週先衝午餐客")
        assert repo.get_shared_context("t1", "brand_brief")["brand_name"] == "好吃小館"


class TestAutomationSettings:
    def _make_repo(self):
        from sqlmodel import create_engine
        from kachu.persistence.tables import SQLModel
        from kachu.persistence.repository import KachuRepository

        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        return KachuRepository(engine)

    def test_update_automation_settings(self):
        repo = self._make_repo()
        default_settings = repo.get_or_create_automation_settings("t1")
        assert default_settings.ga_report_frequency == "weekly"

        updated = repo.update_automation_settings(
            "t1",
            ga_report_frequency="daily",
            google_post_enabled=False,
            proactive_hour=9,
        )

        assert updated.ga_report_frequency == "daily"
        assert updated.google_post_enabled is False
        assert updated.proactive_hour == 9


class TestAutomationSettingsDashboardApi:
    def test_dashboard_can_read_and_update_automation_settings(self):
        from fastapi.testclient import TestClient

        from kachu.config import Settings
        from kachu.main import create_app

        client = TestClient(
            create_app(
                Settings(
                    LINE_CHANNEL_ACCESS_TOKEN="",
                    LINE_CHANNEL_SECRET="",
                    LINE_BOSS_USER_ID="boss-automation",
                    ADMIN_SERVICE_TOKEN="dashboard-token",
                    AGENTOS_BASE_URL="http://agentos-mock",
                    KACHU_BASE_URL="http://localhost:8001",
                    DATABASE_URL="sqlite://",
                )
            )
        )

        headers = {"Authorization": "Bearer dashboard-token"}

        response = client.get("/dashboard/api/automation-settings", headers=headers)
        assert response.status_code == 200
        assert response.json()["ga_report_frequency"] == "weekly"

        updated = client.put(
            "/dashboard/api/automation-settings",
            headers=headers,
            json={
                "timezone": "Asia/Tokyo",
                "ga_report_enabled": True,
                "ga_report_frequency": "daily",
                "ga_report_weekday": "mon",
                "ga_report_hour": 6,
                "google_post_enabled": True,
                "google_post_frequency": "weekly",
                "google_post_weekday": "fri",
                "google_post_hour": 11,
                "proactive_enabled": True,
                "proactive_hour": 8,
                "content_calendar_enabled": True,
                "content_calendar_day": 3,
                "content_calendar_hour": 10,
            },
        )
        assert updated.status_code == 200
        payload = updated.json()
        assert payload["timezone"] == "Asia/Tokyo"
        assert payload["ga_report_frequency"] == "daily"
        assert payload["google_post_weekday"] == "fri"

    def test_dashboard_requires_bearer_token(self):
        from fastapi.testclient import TestClient

        from kachu.config import Settings
        from kachu.main import create_app

        client = TestClient(
            create_app(
                Settings(
                    LINE_CHANNEL_ACCESS_TOKEN="",
                    LINE_CHANNEL_SECRET="",
                    LINE_BOSS_USER_ID="boss-automation",
                    ADMIN_SERVICE_TOKEN="dashboard-token",
                    AGENTOS_BASE_URL="http://agentos-mock",
                    KACHU_BASE_URL="http://localhost:8001",
                    DATABASE_URL="sqlite://",
                )
            )
        )

        response = client.get("/dashboard/api/automation-settings")
        assert response.status_code == 401

    def test_dashboard_rejects_invalid_timezone(self):
        from fastapi.testclient import TestClient

        from kachu.config import Settings
        from kachu.main import create_app

        client = TestClient(
            create_app(
                Settings(
                    LINE_CHANNEL_ACCESS_TOKEN="",
                    LINE_CHANNEL_SECRET="",
                    LINE_BOSS_USER_ID="boss-automation",
                    ADMIN_SERVICE_TOKEN="dashboard-token",
                    AGENTOS_BASE_URL="http://agentos-mock",
                    KACHU_BASE_URL="http://localhost:8001",
                    DATABASE_URL="sqlite://",
                )
            )
        )

        response = client.put(
            "/dashboard/api/automation-settings",
            headers={"Authorization": "Bearer dashboard-token"},
            json={"timezone": "Mars/Phobos"},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == "Invalid timezone"


# ── compute_and_save_approval_profile ────────────────────────────────────────

class TestApprovalProfile:
    def _make_repo(self):
        from sqlmodel import create_engine
        from kachu.persistence.tables import SQLModel
        from kachu.persistence.repository import KachuRepository
        engine = create_engine("sqlite:///:memory:")
        SQLModel.metadata.create_all(engine)
        return KachuRepository(engine)

    def test_empty_decisions(self):
        repo = self._make_repo()
        profile = repo.compute_and_save_approval_profile("t1")
        assert profile.tenant_id == "t1"
        assert profile.total_decisions == 0
        assert profile.recent_acceptance_rate == 0.0

    def test_profile_recompute_updates_existing(self):
        repo = self._make_repo()
        repo.compute_and_save_approval_profile("t1")
        profile2 = repo.compute_and_save_approval_profile("t1")
        assert profile2.tenant_id == "t1"

    def test_get_last_published_at_ignores_non_publish_workflows(self):
        repo = self._make_repo()
        report_run = repo.create_workflow_record(
            tenant_id="t1",
            agentos_run_id="run-report",
            agentos_task_id="task-report",
            workflow_type="ga4_report",
            trigger_source="schedule",
            trigger_payload={},
        )
        photo_run = repo.create_workflow_record(
            tenant_id="t1",
            agentos_run_id="run-photo",
            agentos_task_id="task-photo",
            workflow_type="photo_content",
            trigger_source="line",
            trigger_payload={},
        )
        repo.update_workflow_run_status(report_run.id, "completed")
        repo.update_workflow_run_status(photo_run.id, "completed")

        last_published = repo.get_last_published_at("t1")
        assert last_published is not None


# ── ContentCalendarAgent ─────────────────────────────────────────────────────

class TestContentCalendar:
    def _make_agent(self):
        from kachu.content_calendar import ContentCalendarAgent

        repo = MagicMock()
        repo.get_shared_context.return_value = {}
        repo.list_active_tenant_ids.return_value = ["t1"]
        memory = MagicMock()
        memory.get_recent_episodes = AsyncMock(return_value=[])
        memory.get_preference_examples = AsyncMock(return_value=[])
        settings = MagicMock()
        settings.LITELLM_MODEL = "gemini/test"
        settings.GOOGLE_AI_API_KEY = "fake"
        settings.OPENAI_API_KEY = ""
        return ContentCalendarAgent(repo, memory, settings), repo, memory

    @pytest.mark.asyncio
    async def test_generate_and_save_falls_back_on_recoverable_llm_error(self):
        agent, repo, _memory = self._make_agent()

        with patch("kachu.content_calendar.generate_text", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
            calendar = await agent.generate_and_save("t1")

        assert len(calendar["weeks"]) == 4
        repo.save_shared_context.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_and_save_re_raises_unexpected_llm_error(self):
        agent, repo, _memory = self._make_agent()

        with patch("kachu.content_calendar.generate_text", new=AsyncMock(side_effect=AssertionError("unexpected"))):
            with pytest.raises(AssertionError, match="unexpected"):
                await agent.generate_and_save("t1")

        repo.save_shared_context.assert_not_called()

    @pytest.mark.asyncio
    async def test_scan_all_tenants_logs_recoverable_db_error(self):
        agent, repo, _memory = self._make_agent()

        with patch.object(agent, "generate_and_save", side_effect=SQLAlchemyError("db error")):
            await agent.scan_all_tenants()

    @pytest.mark.asyncio
    async def test_scan_all_tenants_re_raises_unexpected_error(self):
        agent, repo, _memory = self._make_agent()

        with patch.object(agent, "generate_and_save", side_effect=AssertionError("unexpected")):
            with pytest.raises(AssertionError, match="unexpected"):
                await agent.scan_all_tenants()
