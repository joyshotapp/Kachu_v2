"""
Phase 0 整合測試：photo_content 工作流

驗收標準（Product Plan Phase 0）：
  老闆傳一張照片
  → LINE Webhook 識別意圖
  → 觸發 AgentOS kachu_photo_content task
  → AgentOS 執行 analyze-photo → retrieve-context → generate-drafts → notify-approval
  → AgentOS 在 confirm-publish 暫停，等待審批
  → 老闆按確認（LINE postback → ApprovalBridge → AgentOS decide_approval）
  → AgentOS 繼續執行 publish-content（Phase 0 只記錄，不實際發布）
  → Run 狀態變 completed

Run:
    pytest tests/test_photo_content_e2e.py -v
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from kachu.config import Settings
from kachu.main import create_app


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def settings() -> Settings:
    return Settings(
        LINE_CHANNEL_ACCESS_TOKEN="",
        LINE_CHANNEL_SECRET="",
        LINE_BOSS_USER_ID="U_boss_001",
        AGENTOS_BASE_URL="http://agentos-mock",
        KACHU_BASE_URL="http://localhost:8001",
        DATABASE_URL="sqlite://",
        GOOGLE_AI_API_KEY="",
    )


@pytest.fixture
def client(settings: Settings) -> TestClient:
    app = create_app(settings)
    return TestClient(app)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_analyze_photo_stub(client: TestClient) -> None:
    resp = client.post(
        "/tools/analyze-photo",
        json={
            "tenant_id": "tenant-001",
            "photo_url": "http://example.com/photo.jpg",
            "line_message_id": "msg-001",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "analysis_id" in data
    assert "scene_description" in data
    assert data["status"] == "degraded"
    assert data["needs_manual_review"] is True


def test_analyze_photo_degrades_on_recoverable_http_error() -> None:
    app = create_app(
        Settings(
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_001",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="http://localhost:8001",
            DATABASE_URL="sqlite://",
            GOOGLE_AI_API_KEY="test-key",
        )
    )
    client = TestClient(app)

    with patch(
        "kachu.tools.router.analyze_image_url",
        new=AsyncMock(side_effect=httpx.ReadTimeout("timeout")),
    ):
        resp = client.post(
            "/tools/analyze-photo",
            json={
                "tenant_id": "tenant-001",
                "photo_url": "http://example.com/photo.jpg",
                "line_message_id": "msg-001",
            },
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


def test_analyze_photo_re_raises_unexpected_system_error() -> None:
    app = create_app(
        Settings(
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_001",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="http://localhost:8001",
            DATABASE_URL="sqlite://",
            GOOGLE_AI_API_KEY="test-key",
        )
    )
    client = TestClient(app)

    with patch(
        "kachu.tools.router.analyze_image_url",
        new=AsyncMock(side_effect=AssertionError("unexpected")),
    ):
        with pytest.raises(AssertionError, match="unexpected"):
            client.post(
                "/tools/analyze-photo",
                json={
                    "tenant_id": "tenant-001",
                    "photo_url": "http://example.com/photo.jpg",
                    "line_message_id": "msg-001",
                },
            )


def test_retrieve_context_stub(client: TestClient) -> None:
    resp = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-001", "query": "麻辣鴨血"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "brand_name" in data


def test_generate_drafts_stub(client: TestClient) -> None:
    resp = client.post(
        "/tools/generate-drafts",
        json={"tenant_id": "tenant-001"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "ig_fb" in data
    assert "google" in data


def test_notify_approval_stores_pending(client: TestClient) -> None:
    """notify-approval 應將 PendingApproval 存入 DB，並嘗試推播 LINE。"""
    resp = client.post(
        "/tools/notify-approval",
        json={
            "tenant_id": "tenant-001",
            "run_id": "run-phase0-001",
            "workflow": "kachu_photo_content",
            "drafts": {
                "ig_fb": "測試 IG 草稿",
                "google": "測試 Google 商家草稿",
            },
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "notified"
    assert "approval_record_id" in data

    # Verify it's in the DB
    repo = client.app.state.repository
    record = repo.get_pending_approval_by_run_id("run-phase0-001")
    assert record is not None
    assert record.status == "pending"
    assert record.workflow_type == "kachu_photo_content"


def test_publish_content_stub(client: TestClient) -> None:
    """publish-content Phase 0 應回傳 recorded，不報錯。"""
    # First create a pending approval record
    repo = client.app.state.repository
    repo.create_pending_approval(
        tenant_id="tenant-001",
        agentos_run_id="run-phase0-002",
        workflow_type="kachu_photo_content",
        draft_content={"ig_fb": "test", "google": "test"},
    )

    resp = client.post(
        "/tools/publish-content",
        json={
            "tenant_id": "tenant-001",
            "run_id": "run-phase0-002",
            "selected_platforms": ["ig_fb", "google"],
            "drafts": {"ig_fb": "test draft", "google": "test draft"},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "done"
    assert "results" in data


@pytest.mark.asyncio
async def test_approval_bridge_approve() -> None:
    """ApprovalBridge 應將 LINE postback 轉換為 AgentOS decide_approval 呼叫。"""
    from kachu.approval_bridge import ApprovalBridge
    from kachu.models import ApprovalAction
    from unittest.mock import MagicMock, AsyncMock

    mock_agentOS = AsyncMock()
    mock_agentOS.get_pending_approval_id_for_run.return_value = "approval-agentos-001"
    mock_agentOS.decide_approval.return_value = MagicMock(run={"id": "run-001", "status": "running"})

    mock_repo = MagicMock()
    mock_repo.decide_pending_approval.return_value = None

    mock_settings = MagicMock()
    mock_settings.LINE_CHANNEL_ACCESS_TOKEN = ""

    bridge = ApprovalBridge(agentOS_client=mock_agentOS, repository=mock_repo, settings=mock_settings)
    await bridge.handle_postback(
        run_id="run-001",
        tenant_id="tenant-001",
        action=ApprovalAction.APPROVE,
        actor_line_id="U_boss_001",
    )

    mock_agentOS.get_pending_approval_id_for_run.assert_called_once_with("run-001")
    mock_agentOS.decide_approval.assert_called_once()
    call_args = mock_agentOS.decide_approval.call_args
    assert call_args[0][0] == "approval-agentos-001"
    assert call_args[0][1].decision == "approved"
    assert call_args[0][1].actor_id == "U_boss_001"

    mock_repo.decide_pending_approval.assert_called_once_with(
        agentos_run_id="run-001",
        decision="approved",
        actor_line_id="U_boss_001",
    )


@pytest.mark.asyncio
async def test_approval_bridge_reject() -> None:
    from kachu.approval_bridge import ApprovalBridge
    from kachu.models import ApprovalAction
    from unittest.mock import MagicMock, AsyncMock

    mock_agentOS = AsyncMock()
    mock_agentOS.get_pending_approval_id_for_run.return_value = "approval-agentos-002"
    mock_agentOS.decide_approval.return_value = MagicMock(run={"id": "run-002", "status": "failed"})

    mock_repo = MagicMock()

    mock_settings = MagicMock()
    mock_settings.LINE_CHANNEL_ACCESS_TOKEN = ""

    bridge = ApprovalBridge(agentOS_client=mock_agentOS, repository=mock_repo, settings=mock_settings)
    await bridge.handle_postback(
        run_id="run-002",
        tenant_id="tenant-001",
        action=ApprovalAction.REJECT,
        actor_line_id="U_boss_001",
    )

    decision_arg = mock_agentOS.decide_approval.call_args[0][1]
    assert decision_arg.decision == "rejected"


def test_intent_router_classifies_text() -> None:
    from unittest.mock import AsyncMock, MagicMock
    from kachu.intent_router import IntentRouter
    from kachu.models import Intent

    router = IntentRouter(agentOS_client=AsyncMock(), repository=MagicMock())

    assert router.classify_text("雞腿飯改成 90 元了") == Intent.KNOWLEDGE_UPDATE
    assert router.classify_text("你們幾點開始營業？") == Intent.FAQ_QUERY
    assert router.classify_text("今天天氣好好啊") == Intent.GENERAL_CHAT
