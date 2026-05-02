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

import base64
import json
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


def test_retrieve_context_prefers_active_basic_info_and_filters_noise(client: TestClient) -> None:
    repo = client.app.state.repository
    tenant = repo.get_or_create_tenant("tenant-knowledge")
    tenant.name = "四時循養堂"
    tenant.industry_type = "保健食品"
    tenant.address = "新北市泰山區仁義路222號"
    repo.save_tenant(tenant)

    stale = repo.save_knowledge_entry(
        tenant_id="tenant-knowledge",
        category="basic_info",
        content="店名：坐骨新經 陳老師，行業：保健食品，地址：新北市泰山區仁義路222號",
    )
    repo.mark_knowledge_entry_superseded(stale.id)
    repo.save_knowledge_entry(
        tenant_id="tenant-knowledge",
        category="basic_info",
        content="店名：四時循養堂，行業：保健食品，地址：新北市泰山區仁義路222號",
    )
    repo.save_knowledge_entry(tenant_id="tenant-knowledge", category="goal", content="增加品牌知名度")
    repo.save_knowledge_entry(tenant_id="tenant-knowledge", category="pain_point", content="知名度不夠 新品牌")
    repo.save_knowledge_entry(tenant_id="tenant-knowledge", category="document", content="那你覺得目標客群要怎麼設定？")
    repo.save_knowledge_entry(
        tenant_id="tenant-knowledge",
        category="document",
        content="這是品牌資訊：我們主打草本濃縮與日常調理",
    )

    resp = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-knowledge", "query": "你們主打什麼"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["brand_name"] == "四時循養堂"
    assert all("那你覺得" not in item for item in data["relevant_facts"])
    assert any("草本濃縮" in item or "增加品牌知名度" in item for item in data["relevant_facts"])


def test_retrieve_context_operations_query_prioritizes_goal_and_pain_point(client: TestClient) -> None:
    repo = client.app.state.repository
    tenant = repo.get_or_create_tenant("tenant-ops")
    tenant.name = "四時循養堂"
    tenant.industry_type = "保健食品"
    repo.save_tenant(tenant)

    repo.save_knowledge_entry(
        tenant_id="tenant-ops",
        category="basic_info",
        content="店名：四時循養堂，行業：保健食品，地址：新北市泰山區仁義路222號",
    )
    repo.save_knowledge_entry(tenant_id="tenant-ops", category="goal", content="增加品牌知名度")
    repo.save_knowledge_entry(tenant_id="tenant-ops", category="pain_point", content="知名度不夠 新品牌")
    repo.save_knowledge_entry(
        tenant_id="tenant-ops",
        category="document",
        content="這是品牌資訊：我們主打草本濃縮與日常調理\n品項/內容：{'name': '疏通飲', 'description': '漢方濃縮・日常調理・支援行動力'}",
    )

    resp = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-ops", "query": "目前最想改善什麼經營問題"},
    )

    assert resp.status_code == 200
    facts = resp.json()["relevant_facts"]
    assert facts[0] == "知名度不夠 新品牌"
    assert any(item == "增加品牌知名度" for item in facts)


def test_retrieve_context_contact_query_prefers_derived_contact_and_style(client: TestClient) -> None:
    repo = client.app.state.repository
    tenant = repo.get_or_create_tenant("tenant-contact")
    tenant.name = "四時循養堂"
    tenant.industry_type = "保健食品"
    repo.save_tenant(tenant)

    repo.save_knowledge_entry(
        tenant_id="tenant-contact",
        category="document",
        content=(
            "這是品牌資訊：四時循養堂提供日常調理服務。\n"
            "電話：02-1234-5678\n"
            "LINE：@kachu\n"
            "品牌語氣：溫暖、專業、少官腔"
        ),
    )

    resp = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-contact", "query": "要怎麼預約或聯絡你們"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert data["relevant_facts"][0] in {"電話：02-1234-5678", "LINE：@kachu"}
    assert data["brand_tone"].startswith("溫暖")
    assert any(item == "LINE：@kachu" for item in data["contact_points"])
    assert all("【圖片分析】" not in item for item in data["contact_points"])


def test_retrieve_context_offer_facts_skip_raw_document_summary(client: TestClient) -> None:
    repo = client.app.state.repository
    tenant = repo.get_or_create_tenant("tenant-offer")
    tenant.name = "四時循養堂"
    tenant.industry_type = "保健食品"
    repo.save_tenant(tenant)

    repo.save_knowledge_entry(
        tenant_id="tenant-offer",
        category="document",
        content=(
            "【圖片分析】這是一張四時循養堂疏通飲的產品宣傳圖片，現在加入官方LINE好友，可限時0元體驗。\n"
            "品項/內容：{'name': '限時體驗', 'price': '0元'}、{'name': '30包', 'price': '3750元'}\n"
            "關鍵詞：促銷 優惠 折扣 買越多省越多 價格 包裝"
        ),
    )

    resp = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-offer", "query": "最近有什麼優惠"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert any(item == "限時體驗：0元" for item in data["offer_facts"])
    assert all("【圖片分析】" not in item for item in data["offer_facts"])
    assert all("關鍵詞：" not in item for item in data["offer_facts"])


def test_retrieve_context_filters_existing_dirty_contact_and_offer_entries(client: TestClient) -> None:
    repo = client.app.state.repository
    tenant = repo.get_or_create_tenant("tenant-dirty-facts")
    tenant.name = "四時循養堂"
    tenant.industry_type = "保健食品"
    tenant.address = "新北市泰山區仁義路222號"
    repo.save_tenant(tenant)

    repo.save_knowledge_entry(
        tenant_id="tenant-dirty-facts",
        category="contact",
        content="【圖片分析】這是一張四時循養堂疏通飲的產品宣傳圖片，現在加入官方LINE好友，可限時0元體驗",
    )
    repo.save_knowledge_entry(
        tenant_id="tenant-dirty-facts",
        category="offer",
        content="關鍵詞：促銷 優惠 折扣 買越多省越多 價格 包裝",
    )
    repo.save_knowledge_entry(
        tenant_id="tenant-dirty-facts",
        category="offer",
        content="限時體驗：0元",
    )

    resp = client.post(
        "/tools/retrieve-context",
        json={"tenant_id": "tenant-dirty-facts", "query": "最近有什麼優惠"},
    )

    assert resp.status_code == 200
    data = resp.json()
    assert all("【圖片分析】" not in item for item in data["contact_points"])
    assert all("關鍵詞：" not in item for item in data["offer_facts"])
    assert any(item == "限時體驗：0元" for item in data["offer_facts"])


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
    assert json.loads(record.draft_content)["image_url"].endswith(
        "/tools/approval-photo/run-phase0-001"
    )


def test_approval_photo_preview_returns_image_from_workflow_payload(client: TestClient) -> None:
    repo = client.app.state.repository
    image_bytes = b"fake-image-bytes"
    repo.create_workflow_record(
        tenant_id="tenant-001",
        agentos_run_id="run-phase0-photo-preview",
        agentos_task_id="task-001",
        workflow_type="photo_content",
        trigger_source="line",
        trigger_payload={
            "line_message_id": "msg-001",
            "photo_url": "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode(),
        },
    )

    resp = client.get("/tools/approval-photo/run-phase0-photo-preview")

    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == image_bytes


def test_notify_approval_pushes_photo_preview_before_flex() -> None:
    app = create_app(
        Settings(
            LINE_CHANNEL_ACCESS_TOKEN="line-token",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_001",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="https://app.kachu.tw",
            DATABASE_URL="sqlite://",
            GOOGLE_AI_API_KEY="",
        )
    )
    client = TestClient(app)
    repo = client.app.state.repository
    repo.create_workflow_record(
        tenant_id="tenant-001",
        agentos_run_id="run-phase0-photo-push",
        agentos_task_id="task-001",
        workflow_type="photo_content",
        trigger_source="line",
        trigger_payload={
            "line_message_id": "msg-001",
            "photo_url": "data:image/jpeg;base64," + base64.b64encode(b"fake-image").decode(),
        },
    )

    response = httpx.Response(200, request=httpx.Request("POST", "https://api.line.me/v2/bot/message/push"))

    with patch("httpx.AsyncClient.post", new=AsyncMock(return_value=response)) as post_mock:
        resp = client.post(
            "/tools/notify-approval",
            json={
                "tenant_id": "tenant-001",
                "run_id": "run-phase0-photo-push",
                "workflow": "kachu_photo_content",
                "drafts": {
                    "ig_fb": "測試 IG 草稿",
                    "google": "測試 Google 商家草稿",
                },
            },
        )

    assert resp.status_code == 200
    body = json.loads(post_mock.await_args.kwargs["content"].decode())
    assert len(body["messages"]) == 2
    assert body["messages"][0]["type"] == "image"
    assert body["messages"][0]["originalContentUrl"].endswith("/tools/approval-photo/run-phase0-photo-push")
    assert body["messages"][1]["type"] == "flex"


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
