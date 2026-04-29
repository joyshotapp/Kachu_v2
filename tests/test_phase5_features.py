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
