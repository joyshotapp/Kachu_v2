"""
Phase 2 tests:
  - Workflow 6: knowledge update (parse / diff / apply)
  - Workflow 3: google post (generate / publish)
  - Workflow 4: GA4 report (fetch / insights / send)
  - Intent Router: keyword + LLM classification for all 6 intents
  - Push rate limiting: daily cap + quiet hours
  - Google OAuth: connect redirect / callback / status
  - ConnectorAccount CRUD
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import create_engine

# Make agent_platform importable when running from Kachu-v2
_AGENTOS_SRC = Path(__file__).parents[2] / "AgentOS" / "src"
if _AGENTOS_SRC.exists() and str(_AGENTOS_SRC) not in sys.path:
    sys.path.insert(0, str(_AGENTOS_SRC))

from kachu.config import Settings
from kachu.intent_router import IntentRouter
from kachu.main import create_app
from kachu.models import AgentOSTaskRequest, Intent
from kachu.persistence import KachuRepository, create_db_engine, init_db
from kachu.persistence.tables import (
    ConnectorAccountTable,
    KnowledgeEntryTable,
    PushLogTable,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def test_settings():
    return Settings(
        LINE_CHANNEL_ACCESS_TOKEN="",
        LINE_CHANNEL_SECRET="",
        LINE_BOSS_USER_ID="U_boss_phase2",
        AGENTOS_BASE_URL="http://agentos-mock",
        KACHU_BASE_URL="http://localhost:8001",
        DATABASE_URL="sqlite://",
        GOOGLE_AI_API_KEY="",
        OPENAI_API_KEY="",
        GA4_PROPERTY_ID="",
        MAX_PUSH_PER_DAY=3,
        GOOGLE_OAUTH_CLIENT_ID="",
        GOOGLE_OAUTH_CLIENT_SECRET="",
    )


@pytest.fixture()
def engine(test_settings):
    eng = create_db_engine(test_settings.DATABASE_URL)
    init_db(eng)
    return eng


@pytest.fixture()
def repo(engine):
    return KachuRepository(engine)


@pytest.fixture()
def client(test_settings, engine):
    _app = create_app(test_settings, _engine=engine)
    return TestClient(_app)


@pytest.fixture()
def tenant_id():
    return "U_boss_phase2"


# ════════════════════════════════════════════════════════════════════════════
# Intent Router — keyword classification
# ════════════════════════════════════════════════════════════════════════════


class TestIntentRouterKeywords:
    def _router(self):
        return IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=None,
        )

    def test_business_profile_update_keywords(self):
        router = self._router()
        for text in ["幫我更新這項資訊：今天公休", "今天店休", "營業時間改成下午兩點開"]:
            assert router.classify_text(text) == Intent.BUSINESS_PROFILE_UPDATE, text

    def test_knowledge_update_keywords(self):
        router = self._router()
        for text in ["雞腿飯改成90元", "價格更新一下", "刪除舊菜單"]:
            assert router.classify_text(text) == Intent.KNOWLEDGE_UPDATE, text

    def test_google_post_keywords(self):
        router = self._router()
        for text in ["幫我寫個七夕活動動態", "發一篇商家動態", "寫個優惠公告"]:
            assert router.classify_text(text) == Intent.GOOGLE_POST, text

    def test_ga4_report_keywords(self):
        router = self._router()
        for text in ["最近生意怎樣", "幫我看流量報告", "上週業績統計"]:
            assert router.classify_text(text) == Intent.GA4_REPORT, text

    def test_review_reply_keywords(self):
        router = self._router()
        for text in ["幫我回覆評論", "有一則負評怎麼回", "回評價"]:
            assert router.classify_text(text) == Intent.REVIEW_REPLY, text

    def test_faq_keywords(self):
        router = self._router()
        for text in ["幾點開門", "在哪裡", "怎麼停車"]:
            assert router.classify_text(text) == Intent.FAQ_QUERY, text

    def test_general_chat_fallback(self):
        router = self._router()
        assert router.classify_text("你好") == Intent.GENERAL_CHAT
        assert router.classify_text("今天天氣真好") == Intent.GENERAL_CHAT


@pytest.mark.asyncio
async def test_plan_boss_message_marks_small_talk_without_actions(test_settings):
    router = IntentRouter(
        agentOS_client=MagicMock(),
        repository=MagicMock(),
        settings=test_settings,
    )

    route = await router.plan_boss_message("你好")

    assert route.intent == Intent.GENERAL_CHAT
    assert route.small_talk is True
    assert route.actions == []


@pytest.mark.asyncio
async def test_knowledge_update_dispatch_uses_line_message_id_as_idempotency_key():
    agentos = MagicMock()
    agentos.create_task = AsyncMock(return_value=SimpleNamespace(task={"id": "task-1"}))
    agentos.run_task = AsyncMock(return_value=SimpleNamespace(run={"id": "run-1", "status": "running"}))
    repo = MagicMock()
    router = IntentRouter(
        agentOS_client=agentos,
        repository=repo,
        settings=None,
    )

    await router._dispatch_knowledge_update(
        tenant_id="T001",
        trigger_source="line",
        trigger_payload={
            "message": "幫我更新這項資訊：我們今天公休",
            "line_message_id": "msg-knowledge-1",
        },
    )

    task_request = agentos.create_task.await_args.args[0]
    assert task_request.idempotency_key == "T001:knowledge_update:line:msg-knowledge-1"


@pytest.mark.asyncio
async def test_business_profile_update_dispatch_uses_line_message_id_as_idempotency_key():
    agentos = MagicMock()
    agentos.create_task = AsyncMock(return_value=SimpleNamespace(task={"id": "task-1"}))
    agentos.run_task = AsyncMock(return_value=SimpleNamespace(run={"id": "run-1", "status": "running"}))
    repo = MagicMock()
    router = IntentRouter(
        agentOS_client=agentos,
        repository=repo,
        settings=None,
    )

    await router._dispatch_business_profile_update(
        tenant_id="T001",
        trigger_source="line",
        trigger_payload={
            "message": "幫我更新這項資訊：今天公休",
            "line_message_id": "msg-biz-1",
        },
    )

    task_request = agentos.create_task.await_args.args[0]
    assert task_request.idempotency_key == "T001:business_profile_update:line:msg-biz-1"


# ════════════════════════════════════════════════════════════════════════════
# Intent Router — LLM classification
# ════════════════════════════════════════════════════════════════════════════


class TestIntentRouterLLM:
    @pytest.mark.asyncio
    async def test_llm_classify_google_post(self, test_settings):
        mock_settings = MagicMock()
        mock_settings.GOOGLE_AI_API_KEY = "fake"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.LITELLM_MODEL = "gemini/gemini-3-flash-preview"

        router = IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=mock_settings,
        )
        llm_response = json.dumps({"intent": "google_post", "topic": "七夕活動"})
        with patch("kachu.llm.generate_text", new=AsyncMock(return_value=llm_response)):
            intent, topic = await router.classify_text_llm("幫我寫個七夕活動動態")
        assert intent == Intent.GOOGLE_POST
        assert topic == "七夕活動"

    @pytest.mark.asyncio
    async def test_llm_classify_fallback_on_error(self, test_settings):
        mock_settings = MagicMock()
        mock_settings.GOOGLE_AI_API_KEY = "fake"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.LITELLM_MODEL = "gemini/gemini-3-flash-preview"

        router = IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=mock_settings,
        )
        with patch("kachu.llm.generate_text", new=AsyncMock(side_effect=httpx.ReadTimeout("timeout"))):
            intent, topic = await router.classify_text_llm("雞腿飯改成90元")
        # Fallback to keyword classifier
        assert intent == Intent.KNOWLEDGE_UPDATE

    @pytest.mark.asyncio
    async def test_llm_classify_re_raises_unexpected_error(self, test_settings):
        mock_settings = MagicMock()
        mock_settings.GOOGLE_AI_API_KEY = "fake"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.LITELLM_MODEL = "gemini/gemini-3-flash-preview"

        router = IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=mock_settings,
        )
        with patch("kachu.llm.generate_text", new=AsyncMock(side_effect=AssertionError("unexpected"))):
            with pytest.raises(AssertionError, match="unexpected"):
                await router.classify_text_llm("雞腿飯改成90元")

    @pytest.mark.asyncio
    async def test_llm_classify_no_api_key(self):
        router = IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=None,
        )
        intent, topic = await router.classify_text_llm("幫我寫個活動貼文")
        # Should fall back to keyword (GOOGLE_POST matches 幫我寫)
        assert intent == Intent.GOOGLE_POST

    @pytest.mark.asyncio
    async def test_plan_boss_message_defaults_question_to_consult(self):
        mock_settings = MagicMock()
        mock_settings.GOOGLE_AI_API_KEY = "fake"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.LITELLM_MODEL = "gemini/gemini-3-flash-preview"

        router = IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=mock_settings,
        )
        llm_response = json.dumps({"intent": "ga4_report", "topic": "流量下滑"})
        with patch("kachu.llm.generate_text", new=AsyncMock(return_value=llm_response)):
            router._goal_parser.parse = AsyncMock(return_value=[{"label": "幫我拉一份流量報告", "intent": "ga4_report", "topic": ""}])
            decision = await router.plan_boss_message("最近流量掉很多，我想先理解問題在哪？")

        assert decision.mode.value == "consult"
        assert decision.intent == Intent.GA4_REPORT
        assert decision.actions

    @pytest.mark.asyncio
    async def test_plan_boss_message_routes_ambiguous_statement_to_clarify(self):
        mock_settings = MagicMock()
        mock_settings.GOOGLE_AI_API_KEY = "fake"
        mock_settings.OPENAI_API_KEY = ""
        mock_settings.LITELLM_MODEL = "gemini/gemini-3-flash-preview"

        router = IntentRouter(
            agentOS_client=MagicMock(),
            repository=MagicMock(),
            settings=mock_settings,
        )
        llm_response = json.dumps({"intent": "ga4_report", "topic": ""})
        clarify_q = "你說流量掉很多，是要我直接拉報告看數字，還是先討論可能原因？"
        with patch("kachu.llm.generate_text", new=AsyncMock(side_effect=[
            llm_response,   # classify_text_llm 呼叫
            clarify_q,      # _generate_clarify_question 呼叫
        ])):
            router._goal_parser.parse = AsyncMock(return_value=[{"label": "幫我拉一份流量報告", "intent": "ga4_report", "topic": ""}])
            decision = await router.plan_boss_message("最近流量掉很多")

        assert decision.mode.value == "clarify"
        assert decision.intent == Intent.GA4_REPORT
        assert decision.clarify_question
        assert decision.actions == []

    @pytest.mark.asyncio
    async def test_create_and_run_logs_recoverable_dispatch_error(self):
        agentos = AsyncMock()
        agentos.create_task.side_effect = httpx.ReadTimeout("timeout")
        repo = MagicMock()
        router = IntentRouter(agentOS_client=agentos, repository=repo, settings=None)

        await router._create_and_run(
            task_request=AgentOSTaskRequest(
                tenant_id="tenant-1",
                domain="kachu_google_post",
                objective="post",
                workflow_input={"tenant_id": "tenant-1"},
            ),
            workflow_type="google_post",
            tenant_id="tenant-1",
            trigger_source="boss_request",
            trigger_payload={"message": "寫一篇貼文"},
        )

        repo.create_workflow_record.assert_not_called()
        repo.create_deferred_dispatch.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_and_run_re_raises_unexpected_dispatch_error(self):
        agentos = AsyncMock()
        agentos.create_task.side_effect = AssertionError("unexpected")
        repo = MagicMock()
        router = IntentRouter(agentOS_client=agentos, repository=repo, settings=None)

        with pytest.raises(AssertionError, match="unexpected"):
            await router._create_and_run(
                task_request=AgentOSTaskRequest(
                    tenant_id="tenant-1",
                    domain="kachu_google_post",
                    objective="post",
                    workflow_input={"tenant_id": "tenant-1"},
                ),
                workflow_type="google_post",
                tenant_id="tenant-1",
                trigger_source="boss_request",
                trigger_payload={"message": "寫一篇貼文"},
            )


# ════════════════════════════════════════════════════════════════════════════
# Knowledge Update tools
# ════════════════════════════════════════════════════════════════════════════


class TestKnowledgeUpdateTools:
    def test_parse_knowledge_update_stub(self, client, tenant_id):
        resp = client.post("/tools/parse-knowledge-update", json={
            "tenant_id": tenant_id,
            "boss_message": "雞腿飯改成 90 元",
            "run_id": "run-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "parsed_update" in data
        assert data["run_id"] == "run-001"

    def test_diff_knowledge_no_match(self, client, tenant_id):
        resp = client.post("/tools/diff-knowledge", json={
            "tenant_id": tenant_id,
            "parsed_update": {
                "update_type": "modify",
                "category": "product",
                "subject": "雞腿飯",
                "old_value": "80元",
                "new_value": "90元",
                "keywords": ["雞腿飯", "價格"],
            },
            "run_id": "run-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        # No knowledge entries in DB yet → 0 conflicts
        assert data["conflicting_entries"] == []

    def test_diff_knowledge_finds_match(self, client, tenant_id, repo):
        # Pre-seed a knowledge entry
        repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="product",
            content="雞腿飯 80 元，每日限量供應",
            source_type="conversation",
        )
        resp = client.post("/tools/diff-knowledge", json={
            "tenant_id": tenant_id,
            "parsed_update": {
                "update_type": "modify",
                "category": "product",
                "subject": "雞腿飯",
                "old_value": None,
                "new_value": "雞腿飯 90 元",
                "keywords": ["雞腿飯"],
            },
            "run_id": "run-002",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["conflicting_entries"]) == 1
        assert "雞腿飯" in data["conflicting_entries"][0]["content"]

    def test_apply_knowledge_update_supersedes(self, client, tenant_id, repo):
        # Pre-seed entry
        old = repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="product",
            content="雞腿飯 80 元",
            source_type="conversation",
        )
        resp = client.post("/tools/apply-knowledge-update", json={
            "tenant_id": tenant_id,
            "run_id": "run-003",
            "diff": {
                "parsed_update": {
                    "update_type": "modify",
                    "category": "product",
                    "subject": "雞腿飯",
                    "old_value": "80元",
                    "new_value": "雞腿飯 90 元",
                    "keywords": ["雞腿飯"],
                },
                "conflicting_entries": [
                    {"entry_id": old.id, "category": "product", "content": "雞腿飯 80 元"}
                ],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "applied"
        assert old.id in data["superseded_entry_ids"]

    def test_apply_knowledge_update_adds_new(self, client, tenant_id):
        resp = client.post("/tools/apply-knowledge-update", json={
            "tenant_id": tenant_id,
            "run_id": "run-004",
            "diff": {
                "parsed_update": {
                    "update_type": "add",
                    "category": "contact",
                    "subject": "電話",
                    "old_value": None,
                    "new_value": "電話：02-1234-5678",
                    "keywords": ["電話"],
                },
                "conflicting_entries": [],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "applied"
        assert data["superseded_entry_ids"] == []


# ════════════════════════════════════════════════════════════════════════════
# Google Post tools
# ════════════════════════════════════════════════════════════════════════════


class TestGooglePostTools:
    def test_generate_google_post_stub(self, client, tenant_id):
        resp = client.post("/tools/generate-google-post", json={
            "tenant_id": tenant_id,
            "topic": "七夕情人節優惠",
            "post_type": "STANDARD",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "post_text" in data
        assert data["topic"] == "七夕情人節優惠"

    def test_generate_google_post_meta_only_returns_ig_fb_draft(self, client, tenant_id):
        resp = client.post("/tools/generate-google-post", json={
            "tenant_id": tenant_id,
            "topic": "本週調理提醒",
            "selected_platforms": ["ig_fb"],
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["selected_platforms"] == ["ig_fb"]
        assert data["ig_fb"] == data["post_text"]

    def test_generate_google_post_with_context(self, client, tenant_id):
        resp = client.post("/tools/generate-google-post", json={
            "tenant_id": tenant_id,
            "topic": "中秋活動",
            "post_type": "EVENT",
            "context": {
                "brand_name": "好吃小館",
                "brand_tone": "親切溫馨",
                "brand_address": "台北市大安區",
                "core_values": ["用心料理", "在地食材"],
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["post_type"] == "EVENT"

    def test_publish_google_post_no_credentials(self, client, tenant_id):
        resp = client.post("/tools/publish-google-post", json={
            "tenant_id": tenant_id,
            "run_id": "run-gp-001",
            "post_text": "七夕限定優惠！歡迎蒞臨。",
        })
        assert resp.status_code == 200
        data = resp.json()
        # No GBP credentials → skipped
        assert data["status"] == "skipped"

    def test_publish_google_post_empty_text(self, client, tenant_id):
        resp = client.post("/tools/publish-google-post", json={
            "tenant_id": tenant_id,
            "run_id": "run-gp-002",
            "post_text": "",
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    def test_publish_google_post_meta_only_uses_publish_content(self, client, tenant_id):
        resp = client.post("/tools/publish-google-post", json={
            "tenant_id": tenant_id,
            "run_id": "run-gp-003",
            "selected_platforms": ["ig_fb"],
            "drafts": {"ig_fb": "Meta 排程文案"},
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["results"]["ig_fb"]["status"] == "skipped_no_credentials"


# ════════════════════════════════════════════════════════════════════════════
# GA4 Report tools
# ════════════════════════════════════════════════════════════════════════════


class TestGA4ReportTools:
    def test_fetch_ga4_data_no_property_id(self, client, tenant_id):
        resp = client.post("/tools/fetch-ga4-data", json={
            "tenant_id": tenant_id,
            "period": "7daysAgo",
            "run_id": "run-ga4-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["totals"]["sessions"] == 0

    def test_generate_ga4_insights_stub(self, client, tenant_id):
        resp = client.post("/tools/generate-ga4-insights", json={
            "tenant_id": tenant_id,
            "run_id": "run-ga4-002",
            "ga4_data": {
                "period": "7daysAgo",
                "totals": {
                    "sessions": 150,
                    "totalUsers": 120,
                    "screenPageViews": 400,
                    "bounceRate": 0.45,
                },
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        insights = data["insights"]
        assert "summary" in insights
        assert "highlights" in insights
        assert "actions" in insights

    def test_send_ga4_report_no_line(self, client, tenant_id):
        resp = client.post("/tools/send-ga4-report", json={
            "tenant_id": tenant_id,
            "run_id": "run-ga4-003",
            "insights": {
                "insights": {
                    "summary": "本週 150 位使用者",
                    "highlights": ["工作階段：150", "頁面瀏覽：400"],
                    "actions": ["更新商家資訊", "分享 LINE 優惠"],
                }
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        # No LINE credentials → skipped
        assert data["status"] == "skipped"
        assert "insights" in data


# ════════════════════════════════════════════════════════════════════════════
# Push Rate Limiting
# ════════════════════════════════════════════════════════════════════════════


class TestPushRateLimiting:
    def test_can_push_initially(self, repo, tenant_id):
        assert repo.can_push(tenant_id, max_per_day=3) is True

    def test_can_push_hits_daily_limit(self, repo, tenant_id):
        repo.record_push(tenant_id=tenant_id, recipient_line_id="U_boss", message_type="approval")
        repo.record_push(tenant_id=tenant_id, recipient_line_id="U_boss", message_type="approval")
        repo.record_push(tenant_id=tenant_id, recipient_line_id="U_boss", message_type="approval")
        assert repo.can_push(tenant_id, max_per_day=3) is False

    def test_count_pushes_today(self, repo, tenant_id):
        assert repo.count_pushes_today(tenant_id) == 0
        repo.record_push(tenant_id=tenant_id, recipient_line_id="U_boss", message_type="report")
        assert repo.count_pushes_today(tenant_id) == 1

    def test_quiet_hours_block(self, repo, tenant_id):
        # Set quiet hours to cover all 24 hours → always blocked
        assert repo.can_push(tenant_id, max_per_day=99, quiet_hours_start=0, quiet_hours_end=0) is True  # wrap-midnight edge: 0-0 means nothing blocked
        # 0-23 covers hours 0 ≤ h < 23 only, but any current hour in [0,23) is blocked
        current_hour = datetime.now(timezone.utc).hour
        if current_hour < 23:
            assert repo.can_push(tenant_id, max_per_day=99, quiet_hours_start=0, quiet_hours_end=23) is False

    def test_notify_approval_respects_rate_limit(self, client, tenant_id, repo):
        """notify-approval should record a push and stop after MAX_PUSH_PER_DAY."""
        # Pre-fill daily limit
        for _ in range(3):
            repo.record_push(tenant_id=tenant_id, recipient_line_id="U_boss_phase2", message_type="approval")

        # No LINE token → would be skipped anyway, but rate limit is checked first
        resp = client.post("/tools/notify-approval", json={
            "tenant_id": tenant_id,
            "run_id": "run-ra-001",
            "workflow": "kachu_google_post",
            "drafts": {"post_text": "七夕優惠"},
        })
        assert resp.status_code == 200
        # Push count should NOT increase (suppressed)
        assert repo.count_pushes_today(tenant_id) == 3


# ════════════════════════════════════════════════════════════════════════════
# Google OAuth flow
# ════════════════════════════════════════════════════════════════════════════


class TestGoogleOAuth:
    def test_connect_without_client_id_returns_503(self, client):
        resp = client.get("/auth/google/connect?tenant_id=U_boss_phase2", follow_redirects=False)
        assert resp.status_code == 503

    def test_connect_with_client_id_redirects(self):
        settings = Settings(
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_phase2",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="http://localhost:8001",
            DATABASE_URL="sqlite://",
            GOOGLE_OAUTH_CLIENT_ID="fake-client-id",
            GOOGLE_OAUTH_CLIENT_SECRET="fake-secret",
        )
        _app = create_app(settings)
        c = TestClient(_app, follow_redirects=False)
        resp = c.get("/auth/google/connect?tenant_id=U_boss_phase2&platforms=gbp,ga4")
        assert resp.status_code in (302, 307)
        assert "accounts.google.com" in resp.headers.get("location", "")

    def test_callback_invalid_state_returns_400(self, client):
        resp = client.get("/auth/google/callback?code=fake-code&state=invalid-state")
        assert resp.status_code == 400

    def test_connector_status_no_connectors(self, client, tenant_id):
        resp = client.get(f"/auth/status/{tenant_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["connectors"]["google_business"]["connected"] is False
        assert data["connectors"]["ga4"]["connected"] is False
        assert data["readiness"]["channels"]["facebook"]["connected"] is False
        assert data["readiness"]["channels"]["instagram"]["status"] == "pending_connection"

    def test_connector_status_returns_phase0_readiness_summary(self, client, tenant_id):
        repo = client.app.state.repository
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="meta",
            credentials_json=json.dumps(
                {
                    "access_token": "meta-token",
                    "fb_page_id": "fb-page-123",
                    "fb_page_name": "示範粉專",
                    "ig_user_id": "",
                }
            ),
            account_label="Meta (示範粉專)",
        )
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="google_business",
            credentials_json=json.dumps({"access_token": "google-token"}),
            account_label="Google Business",
        )

        resp = client.get(f"/auth/status/{tenant_id}")

        assert resp.status_code == 200
        data = resp.json()
        readiness = data["readiness"]
        assert readiness["channels"]["facebook"]["connected"] is True
        assert readiness["channels"]["facebook"]["label"] == "示範粉專"
        assert readiness["channels"]["instagram"]["status"] == "needs_business_link"
        assert readiness["channels"]["google_business"]["connected"] is True
        assert "Facebook" in readiness["next_step"]


# ════════════════════════════════════════════════════════════════════════════
# ConnectorAccount CRUD
# ════════════════════════════════════════════════════════════════════════════


class TestConnectorAccount:
    def test_save_and_get_connector(self, repo, tenant_id):
        creds = json.dumps({"access_token": "tok_abc", "refresh_token": "ref_xyz"})
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="google_business",
            credentials_json=creds,
            account_label="GBP Test",
        )
        account = repo.get_connector_account(tenant_id, "google_business")
        assert account is not None
        assert account.platform == "google_business"
        stored = json.loads(account.credentials_encrypted)
        assert stored["access_token"] == "tok_abc"

    def test_upsert_connector_updates_token(self, repo, tenant_id):
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="ga4",
            credentials_json=json.dumps({"access_token": "tok_old"}),
        )
        repo.save_connector_account(
            tenant_id=tenant_id,
            platform="ga4",
            credentials_json=json.dumps({"access_token": "tok_new"}),
        )
        account = repo.get_connector_account(tenant_id, "ga4")
        assert json.loads(account.credentials_encrypted)["access_token"] == "tok_new"

    def test_get_nonexistent_connector(self, repo, tenant_id):
        account = repo.get_connector_account(tenant_id, "meta")
        assert account is None


# ════════════════════════════════════════════════════════════════════════════
# Knowledge supersede helpers
# ════════════════════════════════════════════════════════════════════════════


class TestKnowledgeSupersede:
    def test_supersede_marks_old_as_superseded(self, repo, tenant_id):
        old = repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="product",
            content="雞腿飯 80 元",
            source_type="conversation",
        )
        new = repo.supersede_knowledge_entry(
            old_entry_id=old.id,
            tenant_id=tenant_id,
            category="product",
            new_content="雞腿飯 90 元",
        )
        # Old entry status should be "superseded"
        from sqlmodel import Session, select
        from kachu.persistence.tables import KnowledgeEntryTable
        engine = repo._engine
        with Session(engine) as session:
            refreshed_old = session.get(KnowledgeEntryTable, old.id)
            assert refreshed_old.status == "superseded"
        assert new.content == "雞腿飯 90 元"
        assert new.status == "active"

    def test_search_by_keywords(self, repo, tenant_id):
        repo.save_knowledge_entry(
            tenant_id=tenant_id, category="product", content="招牌麻辣鍋 350 元", source_type="conversation"
        )
        repo.save_knowledge_entry(
            tenant_id=tenant_id, category="product", content="雞腿飯 80 元", source_type="conversation"
        )
        results = repo.search_knowledge_entries_by_keywords(
            tenant_id=tenant_id, keywords=["麻辣"], categories=["product"]
        )
        assert len(results) == 1
        assert "麻辣" in results[0].content


class TestBusinessProfileUpdateTools:
    def test_parse_business_profile_update_today_closed(self, client, tenant_id):
        resp = client.post("/tools/parse-business-profile-update", json={
            "tenant_id": tenant_id,
            "boss_message": "幫我更新這項資訊：今天公休",
            "run_id": "run-biz-001",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["parsed_update"]["field"] == "今日營業狀態"
        assert data["parsed_update"]["new_value"] == "公休"

    def test_apply_business_profile_update_saves_shared_context_without_knowledge_entry(self, client, tenant_id, repo):
        resp = client.post("/tools/apply-business-profile-update", json={
            "tenant_id": tenant_id,
            "run_id": "run-biz-002",
            "update_request": {
                "boss_message": "幫我更新這項資訊：今天公休",
                "parsed_update": {
                    "update_type": "special_hours",
                    "field": "今日營業狀態",
                    "new_value": "公休",
                    "effective_date": "2026-05-02",
                    "status": "closed",
                    "channel_targets": ["google_business"],
                },
            },
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["shared_context_type"] == "business_profile_state_override"
        ctx = repo.get_shared_context(tenant_id, "business_profile_state_override")
        assert ctx is not None
        assert ctx["parsed_update"]["new_value"] == "公休"
        assert repo.get_knowledge_entries(tenant_id) == []


# ════════════════════════════════════════════════════════════════════════════
# AgentOS pipeline definitions (smoke tests)
# ════════════════════════════════════════════════════════════════════════════


class TestAgentOSPipelines:
    def test_business_profile_update_pipeline_builds_plan(self):
        from agent_platform.models import TaskCreateRequest
        from agent_platform.kachu_workflows.business_profile_update_pipeline import (
            build_kachu_business_profile_update_plan,
        )
        req = TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_business_profile_update",
            objective="update special hours",
            workflow_input={"tenant_id": "T001", "boss_message": "今天公休"},
        )
        plan = build_kachu_business_profile_update_plan(req)
        step_names = [s.name for s in plan.steps]
        assert step_names == [
            "parse-business-profile-update",
            "notify-approval",
            "confirm-business-profile-update",
            "apply-business-profile-update",
        ]

    def test_knowledge_update_pipeline_builds_plan(self):
        from agent_platform.models import TaskCreateRequest
        from agent_platform.kachu_workflows.knowledge_update_pipeline import (
            build_kachu_knowledge_update_plan,
        )
        req = TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_knowledge_update",
            objective="update price",
            workflow_input={"tenant_id": "T001", "boss_message": "雞腿飯改成90元"},
        )
        plan = build_kachu_knowledge_update_plan(req)
        step_names = [s.name for s in plan.steps]
        assert step_names == [
            "parse-knowledge-update",
            "diff-knowledge",
            "notify-approval",
            "confirm-knowledge-update",
            "apply-knowledge-update",
        ]

    def test_google_post_pipeline_builds_plan(self):
        from agent_platform.models import TaskCreateRequest
        from agent_platform.kachu_workflows.google_post_pipeline import (
            build_kachu_google_post_plan,
        )
        req = TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_google_post",
            objective="seven xi post",
            workflow_input={"tenant_id": "T001", "topic": "七夕情人節"},
        )
        plan = build_kachu_google_post_plan(req)
        step_names = [s.name for s in plan.steps]
        assert step_names == [
            "determine-post-type",
            "retrieve-context",
            "generate-google-post",
            "notify-approval",
            "confirm-google-post",
            "publish-google-post",
        ]

    def test_ga4_report_pipeline_builds_plan(self):
        from agent_platform.models import TaskCreateRequest
        from agent_platform.kachu_workflows.ga4_report_pipeline import (
            build_kachu_ga4_report_plan,
        )
        req = TaskCreateRequest(
            tenant_id="T001",
            domain="kachu_ga4_report",
            objective="weekly report",
            workflow_input={"tenant_id": "T001", "period": "7daysAgo"},
        )
        plan = build_kachu_ga4_report_plan(req)
        step_names = [s.name for s in plan.steps]
        assert step_names == ["fetch-ga4-data", "generate-ga4-insights", "generate-recommendations", "send-ga4-report"]

    def test_all_workflows_in_init(self):
        from agent_platform.kachu_workflows import (
            kachu_business_profile_update_workflow_definition,
            kachu_knowledge_update_workflow_definition,
            kachu_google_post_workflow_definition,
            kachu_ga4_report_workflow_definition,
        )
        assert kachu_business_profile_update_workflow_definition().domain == "kachu_business_profile_update"
        assert kachu_knowledge_update_workflow_definition().domain == "kachu_knowledge_update"
        assert kachu_google_post_workflow_definition().domain == "kachu_google_post"
        assert kachu_ga4_report_workflow_definition().domain == "kachu_ga4_report"
