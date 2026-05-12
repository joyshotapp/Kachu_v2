"""
任務 1-5 驗收測試：三種 intent 回應路徑。

完成條件：
- EXECUTE → logger.info 含 [EXECUTE] 和 intent_label
- CONSULT → logger.info 含 [CONSULT] 和回覆文字
- CLARIFY → logger.info 含 [CLARIFY] 和追問問題
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kachu_plus.line.webhook import router
from kachu_plus.meta import MetaConnectorError
from kachu_plus.models import ExecutionTaskResult
from kachu_plus.persistence.tables import CustomerProfileTable, LineChannelConfigTable, OnboardingStateTable, TenantTable
from kachu_plus.services import UnsupportedExecutionIntentError

_TENANT_ID = "tenant-xyz"
_SECRET = "secret123"


def _sig(body: bytes) -> str:
    digest = hmac.new(_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _make_app(onboarding_complete: bool = True) -> tuple[FastAPI, MagicMock, MagicMock, MagicMock]:
    app = FastAPI()
    repo = MagicMock()
    execute_dispatcher = MagicMock()
    execute_dispatcher.dispatch = AsyncMock(
        return_value=ExecutionTaskResult(
            task_id="task-1",
            domain="kachu_google_post",
            status="created",
            objective="Create a Google post draft",
        )
    )
    execute_dispatcher.get_task = AsyncMock(return_value=SimpleNamespace(task={"status": "running", "current_run_id": "run-1"}))
    execute_dispatcher.get_run = AsyncMock(return_value=SimpleNamespace(run={"status": "running"}))
    consultant = MagicMock()
    consultant.build_reply = AsyncMock(return_value="近一週 Facebook 觸及與互動都有維持，下一步建議把高互動貼文加上預約 CTA。")
    meta_insights_service = MagicMock()
    meta_insights_service.fetch_insights.return_value = {
        "facebook_page_insights": {"page_impressions_unique": 1800, "page_post_engagements": 240}
    }
    meta_oauth_flow_service = MagicMock()
    meta_oauth_flow_service.get_connection_status.return_value = {
        "connected": True,
        "connector": {"account_label": "四時循養堂", "fb_page_id": "fb-page-1", "ig_user_id": "ig-user-1"},
        "active_sessions": [],
    }
    meta_oauth_flow_service.disconnect.return_value = {"status": "disconnected"}
    repo.get_line_channel_config.return_value = LineChannelConfigTable(
        id="cfg", tenant_id=_TENANT_ID, channel_secret=_SECRET,
        channel_access_token="token", channel_id="ch1", is_active=True,
    )
    repo.is_onboarding_complete.return_value = onboarding_complete
    repo.get_or_create_onboarding_state.return_value = OnboardingStateTable(
        id="s1", tenant_id=_TENANT_ID, step="completed" if onboarding_complete else "new"
    )
    repo.get_tenant.return_value = TenantTable(
        id=_TENANT_ID, name="測試店", industry_type="餐廳",
        address="台北市", sleep_threshold=30, is_active=True,
    )
    repo.list_recent_conversations.return_value = []
    repo.get_latest_execute_task_for_tenant.return_value = None
    repo.get_latest_execute_task_record.return_value = None
    repo.list_sleeping_customer_profiles.return_value = [
        CustomerProfileTable(
            id="p1",
            tenant_id=_TENANT_ID,
            display_name="王小美",
            sleep_since_days=67,
            status="sleeping",
        )
    ]
    app.state.repository = repo
    app.state.settings = MagicMock(LINE_CHANNEL_ACCESS_TOKEN="token", LINE_BOSS_USER_ID="")
    app.state.execute_dispatcher = execute_dispatcher
    app.state.consultant = consultant
    app.state.meta_insights_service = meta_insights_service
    app.state.meta_oauth_flow_service = meta_oauth_flow_service
    app.state.settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    app.include_router(router)
    return app, repo, execute_dispatcher, consultant


def _post_event(client: TestClient, text: str) -> Any:
    events = [{"type": "message", "source": {"userId": "U123"}, "message": {"type": "text", "text": text}}]
    body = json.dumps({"events": events}).encode()
    return client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": _sig(body), "Content-Type": "application/json"},
    )


# ── EXECUTE path ──────────────────────────────────────────────────────────────


def test_execute_path_logs_dispatch() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "幫我寫一篇貼文")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "EXECUTE" in log_calls
    assert "google_post" in log_calls
    execute_dispatcher.dispatch.assert_awaited_once()


def test_execute_sleep_query_logs_correct_intent() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "哪些客人超過60天沒來")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "EXECUTE" in log_calls
    assert "sleep_customer_query" in log_calls
    assert "王小美" in log_calls
    execute_dispatcher.dispatch.assert_not_called()


def test_execute_meta_insights_logs_summary_without_dispatch() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log, patch("kachu_plus.line.webhook.deliver_meta_insights_report", new=AsyncMock(return_value={"status": "sent"})) as mock_deliver:
        resp = _post_event(client, "幫我看 Facebook 成效")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "meta_insights" in log_calls
    assert "預約 CTA" in log_calls
    execute_dispatcher.dispatch.assert_not_called()
    mock_deliver.assert_awaited_once()


def test_execute_meta_connect_returns_manage_link_without_dispatch() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "我要連接 FB/IG")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "meta_connect" in log_calls
    assert "plus.kachu.tw" in log_calls
    execute_dispatcher.dispatch.assert_not_called()


def test_execute_meta_status_returns_current_connector_without_dispatch() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "我現在連的是哪個粉專")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "meta_status" in log_calls
    assert "四時循養堂" in log_calls
    execute_dispatcher.dispatch.assert_not_called()


def test_execute_meta_disconnect_deactivates_without_dispatch() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "我要解除 Meta 連接")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "meta_disconnect" in log_calls
    assert "已解除" in log_calls
    execute_dispatcher.dispatch.assert_not_called()


def test_execute_draft_status_returns_task_progress_without_dispatch() -> None:
    app, repo, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)
    repo.get_latest_execute_task_record.return_value = SimpleNamespace(
        task_id="task-1",
        run_id="run-1",
        status="running",
        intent_label="google_post",
    )
    repo.get_pending_approval_by_run_id.return_value = None

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "草稿好了嗎")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "draft_status" in log_calls
    execute_dispatcher.dispatch.assert_not_called()
    execute_dispatcher.get_task.assert_awaited_once()


def test_execute_draft_status_resends_pending_approval_card_when_waiting_approval() -> None:
    app, repo, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)
    repo.get_latest_execute_task_record.return_value = SimpleNamespace(
        task_id="task-1",
        run_id="run-1",
        status="waiting_approval",
        intent_label="photo_content",
    )
    repo.get_pending_approval_by_run_id.return_value = SimpleNamespace(
        status="delivery_failed",
        workflow_type="kachu_photo_content",
        agentos_run_id="run-1",
        tenant_id=_TENANT_ID,
        draft_content=json.dumps({"ig_fb": "IG 草稿", "google": "Google 草稿"}, ensure_ascii=False),
    )
    execute_dispatcher.get_task = AsyncMock(return_value=SimpleNamespace(task={"status": "waiting_approval", "current_run_id": "run-1"}))
    execute_dispatcher.get_run = AsyncMock(return_value=SimpleNamespace(run={"status": "waiting_approval"}))

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "草稿好了嗎")

    assert resp.status_code == 200
    execute_dispatcher.dispatch.assert_not_called()
    messages = push_mock.await_args.kwargs["messages"]
    assert "通知沒有成功送達" in messages[0]["text"]
    assert messages[1]["type"] == "flex"


def test_execute_website_ingest_returns_summary_without_dispatch() -> None:
    app, _, execute_dispatcher, _ = _make_app(onboarding_complete=True)
    client = TestClient(app)
    mock_service = MagicMock()
    mock_service.ingest_from_message = AsyncMock(return_value=SimpleNamespace(
        source_url="https://seasonwell.com.tw",
        brand_name="四時循養堂",
        summary="主打漢方保健與日常調理。",
        highlights=["筋骨保養", "漢方保健食品"],
        contact_points=[],
        page_urls=["https://seasonwell.com.tw"],
    ))

    with patch("kachu_plus.line.webhook.WebsiteKnowledgeIngestionService", return_value=mock_service):
        resp = _post_event(client, "給你官網可以嗎？ https://seasonwell.com.tw/")

    assert resp.status_code == 200
    execute_dispatcher.dispatch.assert_not_called()
    mock_service.ingest_from_message.assert_awaited_once()


# ── CONSULT path ──────────────────────────────────────────────────────────────


def test_consult_path_logs_reply() -> None:
    app, _, _, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "你覺得我要怎麼提升回購率？")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "CONSULT" in log_calls
    consultant.build_reply.assert_awaited_once()
    assert "reply_directive" in consultant.build_reply.await_args.kwargs
    assert "直接回答老闆這句話真正想問的事" in consultant.build_reply.await_args.kwargs["reply_directive"]


def test_capability_question_uses_router_consult_reply_without_consultant() -> None:
    app, _, _, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "你能做什麼？")

    assert resp.status_code == 200
    consultant.build_reply.assert_not_called()
    messages = push_mock.await_args.kwargs["messages"]
    assert "Google 商家動態" in messages[0]["text"]


def test_greeting_uses_router_consult_reply_without_consultant() -> None:
    app, _, _, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "你好")

    assert resp.status_code == 200
    consultant.build_reply.assert_not_called()
    messages = push_mock.await_args.kwargs["messages"]
    assert messages[0]["text"].startswith("你好，我在")


def test_content_angle_question_routes_to_consultant() -> None:
    app, _, _, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "我想做母親節，但不知道怎麼切角度")

    assert resp.status_code == 200
    consultant.build_reply.assert_awaited_once()
    messages = push_mock.await_args.kwargs["messages"]
    assert len(messages[0]["text"]) > 0


# ── CLARIFY path ──────────────────────────────────────────────────────────────


def test_clarify_path_logs_question() -> None:
    app, _, execute_dispatcher, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "最近生意")

    assert resp.status_code == 200
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "CLARIFY" in log_calls
    execute_dispatcher.dispatch.assert_not_called()
    consultant.build_reply.assert_not_called()


def test_clarify_traffic_question_becomes_targeted_question() -> None:
    app, _, execute_dispatcher, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "最近流量掉很多")

    assert resp.status_code == 200
    execute_dispatcher.dispatch.assert_not_called()
    consultant.build_reply.assert_not_called()
    messages = push_mock.await_args.kwargs["messages"]
    assert "拉報告看數字" in messages[0]["text"]
    assert "拆可能原因" in messages[0]["text"]


def test_clarify_review_question_becomes_targeted_question() -> None:
    app, _, execute_dispatcher, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "有個評論")

    assert resp.status_code == 200
    execute_dispatcher.dispatch.assert_not_called()
    consultant.build_reply.assert_not_called()
    messages = push_mock.await_args.kwargs["messages"]
    assert "直接幫你回這則評論" in messages[0]["text"]
    assert "怎麼處理比較好" in messages[0]["text"]


def test_emotional_clarify_adds_empathy_prefix() -> None:
    app, _, execute_dispatcher, consultant = _make_app(onboarding_complete=True)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as push_mock:
        resp = _post_event(client, "最近生意很差，我有點焦慮")

    assert resp.status_code == 200
    execute_dispatcher.dispatch.assert_not_called()
    consultant.build_reply.assert_not_called()
    messages = push_mock.await_args.kwargs["messages"]
    assert "我知道你現在有點擔心" in messages[0]["text"]


# ── Onboarding path ───────────────────────────────────────────────────────────


def test_onboarding_tenant_routes_to_onboarding() -> None:
    """尚未完成 onboarding 的 tenant，訊息應進入 onboarding flow，不走 intent router。"""
    app, repo, execute_dispatcher, consultant = _make_app(onboarding_complete=False)
    client = TestClient(app)

    with patch("kachu_plus.line.webhook.logger") as mock_log:
        resp = _post_event(client, "我的店叫好味咖啡")

    assert resp.status_code == 200
    # EXECUTE/CONSULT/CLARIFY 都不應出現在 log（應該是 onboarding reply）
    log_calls = " ".join(str(c) for c in mock_log.info.call_args_list)
    assert "EXECUTE" not in log_calls
    assert "CONSULT" not in log_calls
    assert "CLARIFY" not in log_calls
    execute_dispatcher.dispatch.assert_not_called()
    consultant.build_reply.assert_not_called()
