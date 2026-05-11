"""
任務 1-2 驗收測試：LINE webhook endpoint。

完成條件：
1. signature 驗證失敗時回 403
2. tenant_id 不存在時回 404
3. 正確 tenant 收到事件後有可觀察的輸出（logger.info 被呼叫）
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, AsyncMock, patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine
from sqlalchemy.pool import StaticPool

from kachu_plus.line.webhook import _handle_tag_management, _push_and_record_texts, router
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.config import Settings
from kachu_plus.persistence.tables import CustomerProfileTable, LineChannelConfigTable, OnboardingStateTable, TenantTable

_TENANT_ID = "tenant-abc"
_CHANNEL_SECRET = "test_channel_secret"


def _make_signature(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _make_app(config: LineChannelConfigTable | None) -> FastAPI:
    app = FastAPI()
    repo = MagicMock()
    repo.get_line_channel_config.return_value = config
    repo.record_webhook_event_if_new.return_value = True
    app.state.repository = repo
    app.include_router(router)
    return app


def _valid_config() -> LineChannelConfigTable:
    return LineChannelConfigTable(
        id="cfg-1",
        tenant_id=_TENANT_ID,
        channel_secret=_CHANNEL_SECRET,
        channel_access_token="token",
        channel_id="ch1",
        is_active=True,
    )


def _make_body(events: list[dict[str, Any]] | None = None) -> bytes:
    return json.dumps({"events": events or []}).encode()


# ── 完成條件 2：tenant_id 不存在 → 404 ──────────────────────────────────────


def test_unknown_tenant_returns_404() -> None:
    app = _make_app(config=None)
    client = TestClient(app, raise_server_exceptions=False)

    body = _make_body()
    sig = _make_signature(body, _CHANNEL_SECRET)
    resp = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 404


# ── 完成條件 1：signature 驗證失敗 → 403 ────────────────────────────────────


def test_invalid_signature_returns_403() -> None:
    app = _make_app(config=_valid_config())
    client = TestClient(app, raise_server_exceptions=False)

    body = _make_body()
    resp = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": "invalid_sig", "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


def test_forged_signature_returns_403() -> None:
    app = _make_app(config=_valid_config())
    client = TestClient(app, raise_server_exceptions=False)

    body = _make_body()
    # 用錯誤的 secret 簽名，模擬偽造
    forged_sig = _make_signature(body, "wrong_secret")
    resp = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": forged_sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 403


# ── 完成條件 3：正確 tenant + 正確 signature → 200 + logger 被呼叫 ──────────


def test_valid_request_returns_ok_and_logs_event() -> None:
    app = _make_app(config=_valid_config())
    client = TestClient(app, raise_server_exceptions=False)

    events = [
        {
            "type": "message",
            "source": {"type": "user", "userId": "Uabc123"},
            "message": {"type": "text", "text": "你好"},
        }
    ]
    body = _make_body(events)
    sig = _make_signature(body, _CHANNEL_SECRET)

    with patch("kachu_plus.line.webhook.logger") as mock_logger:
        resp = client.post(
            f"/webhooks/line/{_TENANT_ID}",
            content=body,
            headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
        )

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert mock_logger.info.call_count >= 1
    # 確認 log 有 tenant_id 和 LINE user
    all_calls = " ".join(str(c) for c in mock_logger.info.call_args_list)
    assert _TENANT_ID in all_calls
    assert "Uabc123" in all_calls
    app.state.repository.resolve_or_create_line_profile.assert_called_once_with(_TENANT_ID, "Uabc123")


def test_empty_events_returns_ok() -> None:
    """LINE 有時送空 events（例如 verify webhook 呼叫）"""
    app = _make_app(config=_valid_config())
    client = TestClient(app, raise_server_exceptions=False)

    body = _make_body(events=[])
    sig = _make_signature(body, _CHANNEL_SECRET)
    resp = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_push_and_record_texts_records_only_after_successful_push() -> None:
    repo = MagicMock()

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as mock_push:
        await _push_and_record_texts(
            repo=repo,
            tenant_id=_TENANT_ID,
            line_user_id="Uabc123",
            conversation_kind="follow_up",
            messages=[{"type": "text", "text": "已成功送出"}],
            access_token="token",
        )

    mock_push.assert_awaited_once()
    assert repo.save_conversation.call_count == 2
    audit_call = repo.save_conversation.call_args_list[0].kwargs
    ai_call = repo.save_conversation.call_args_list[1].kwargs
    assert audit_call["actor_role"] == "system"
    assert audit_call["conversation_kind"] == "delivery_audit"
    assert audit_call["metadata"]["delivery_status"] == "success"
    assert ai_call["actor_role"] == "ai"


@pytest.mark.asyncio
async def test_push_and_record_texts_skips_record_when_push_fails() -> None:
    repo = MagicMock()
    request = httpx.Request("POST", "https://api.line.me/v2/bot/message/push")
    response = httpx.Response(500, request=request, text="boom")

    with patch(
        "kachu_plus.line.webhook.push_line_messages",
        new=AsyncMock(side_effect=httpx.HTTPStatusError("push failed", request=request, response=response)),
    ):
        await _push_and_record_texts(
            repo=repo,
            tenant_id=_TENANT_ID,
            line_user_id="Uabc123",
            conversation_kind="follow_up",
            messages=[{"type": "text", "text": "不該先記錄"}],
            access_token="token",
        )

    repo.save_conversation.assert_called_once()
    audit_call = repo.save_conversation.call_args.kwargs
    assert audit_call["actor_role"] == "system"
    assert audit_call["conversation_kind"] == "delivery_audit"
    assert audit_call["metadata"]["delivery_status"] == "failed"
    assert audit_call["metadata"]["delivery_detail"] == "http_500"


@pytest.mark.asyncio
async def test_push_and_record_texts_records_skipped_delivery_audit() -> None:
    repo = MagicMock()

    await _push_and_record_texts(
        repo=repo,
        tenant_id=_TENANT_ID,
        line_user_id="Uabc123",
        conversation_kind="follow_up",
        messages=[{"type": "text", "text": "會被跳過"}],
        access_token="",
    )

    repo.save_conversation.assert_called_once()
    audit_call = repo.save_conversation.call_args.kwargs
    assert audit_call["actor_role"] == "system"
    assert audit_call["conversation_kind"] == "delivery_audit"
    assert audit_call["metadata"]["delivery_status"] == "skipped"


def test_valid_request_resolves_existing_line_profile_without_duplicate_creation() -> None:
    app = _make_app(config=_valid_config())
    profile = CustomerProfileTable(id="p1", tenant_id=_TENANT_ID, interaction_count=1)
    app.state.repository.resolve_or_create_line_profile.return_value = profile
    client = TestClient(app, raise_server_exceptions=False)

    events = [
        {
            "type": "message",
            "source": {"type": "user", "userId": "Uabc123"},
            "message": {"type": "text", "text": "第二次來訊"},
        }
    ]
    body = _make_body(events)
    sig = _make_signature(body, _CHANNEL_SECRET)

    resp = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )

    assert resp.status_code == 200
    app.state.repository.resolve_or_create_line_profile.assert_called_once_with(_TENANT_ID, "Uabc123")


def test_duplicate_webhook_event_is_skipped_before_processing() -> None:
    app = _make_app(config=_valid_config())
    profile = CustomerProfileTable(id="p1", tenant_id=_TENANT_ID, interaction_count=1)
    app.state.repository.resolve_or_create_line_profile.return_value = profile
    app.state.repository.record_webhook_event_if_new.side_effect = [True, False]
    client = TestClient(app, raise_server_exceptions=False)

    events = [
        {
            "type": "message",
            "timestamp": 1714895904000,
            "source": {"type": "user", "userId": "Uabc123"},
            "message": {"id": "mid-1", "type": "text", "text": "你好"},
        }
    ]
    body = _make_body(events)
    sig = _make_signature(body, _CHANNEL_SECRET)

    first = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )
    second = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    app.state.repository.resolve_or_create_line_profile.assert_called_once_with(_TENANT_ID, "Uabc123")


def test_line_webhook_records_event_envelope_metadata() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(engine) as session:
        session.add(TenantTable(id=_TENANT_ID, name="測試店"))
        session.add(_valid_config())
        session.commit()

    app = FastAPI()
    app.state.repository = repo
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    events = [
        {
            "type": "message",
            "timestamp": 1714895904000,
            "source": {"type": "user", "userId": "Uabc123"},
            "message": {"id": "mid-1", "type": "text", "text": "你好"},
        }
    ]
    body = _make_body(events)
    sig = _make_signature(body, _CHANNEL_SECRET)

    response = client.post(
        f"/webhooks/line/{_TENANT_ID}",
        content=body,
        headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
    )

    assert response.status_code == 200
    stored = repo.list_webhook_events(_TENANT_ID, provider="line")
    assert len(stored) == 1
    assert stored[0].external_event_id == "mid-1"
    assert stored[0].external_user_id == "Uabc123"
    assert stored[0].external_thread_id == "mid-1"
    assert stored[0].occurred_at is not None
    assert stored[0].received_at is not None


def test_boss_message_auto_promotes_into_knowledge_entries() -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(engine) as session:
        session.add(TenantTable(id=_TENANT_ID, name="測試店", industry_type="保健食品"))
        session.add(OnboardingStateTable(tenant_id=_TENANT_ID, step="completed"))
        session.add(_valid_config())
        session.commit()

    app = FastAPI()
    app.state.repository = repo
    app.state.settings = Settings()
    app.include_router(router)
    client = TestClient(app, raise_server_exceptions=False)

    body = _make_body(
        [
            {
                "type": "message",
                "timestamp": 1714895904000,
                "source": {"type": "user", "userId": "U-owner-1"},
                "message": {
                    "id": "mid-knowledge-1",
                    "type": "text",
                    "text": "最近很多客人都在意安全性，也會追問有沒有副作用。",
                },
            }
        ]
    )
    sig = _make_signature(body, _CHANNEL_SECRET)

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()):
        response = client.post(
            f"/webhooks/line/{_TENANT_ID}",
            content=body,
            headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
        )

    assert response.status_code == 200
    knowledge = repo.list_knowledge_entries(_TENANT_ID, limit=10)
    assert any(item.category == "pain_point" for item in knowledge)
    assert any("安全性" in item.content for item in knowledge)


@pytest.mark.asyncio
async def test_tag_management_accepts_natural_language_create_phrase() -> None:
    repo = MagicMock()
    repo.create_tag.return_value = MagicMock(name="VIP 顧客")

    with patch("kachu_plus.line.webhook.push_line_messages", new=AsyncMock()) as mock_push:
        await _handle_tag_management(
            tenant_id=_TENANT_ID,
            text="幫我建一個 VIP 顧客標籤",
            line_user_id="Uabc123",
            repo=repo,
            channel_access_token="token",
        )

    repo.create_tag.assert_called_once_with(_TENANT_ID, name="VIP 顧客")
    mock_push.assert_awaited_once()


def test_customer_handoff_lock_blocks_line_faq_auto_reply() -> None:
    app = _make_app(config=_valid_config())
    app.state.repository.list_active_memberships.return_value = [SimpleNamespace(line_user_id="Ucust001", role="customer")]
    app.state.repository.resolve_or_create_line_profile.return_value = CustomerProfileTable(id="p1", tenant_id=_TENANT_ID, interaction_count=1)
    app.state.repository.get_active_conversation_handoff_lock.return_value = SimpleNamespace(reason="人工接手中")
    client = TestClient(app, raise_server_exceptions=False)

    body = _make_body(
        [
            {
                "type": "message",
                "source": {"type": "user", "userId": "Ucust001"},
                "message": {"id": "m-1", "type": "text", "text": "請問今天有開嗎"},
            }
        ]
    )
    sig = _make_signature(body, _CHANNEL_SECRET)

    with patch("kachu_plus.line.webhook.run_line_faq_flow", new=AsyncMock()) as mock_faq:
        response = client.post(
            f"/webhooks/line/{_TENANT_ID}",
            content=body,
            headers={"X-Line-Signature": sig, "Content-Type": "application/json"},
        )

    assert response.status_code == 200
    mock_faq.assert_not_awaited()
    app.state.repository.save_conversation.assert_called()
    saved_metadata = app.state.repository.save_conversation.call_args.kwargs["metadata"]
    assert saved_metadata["handoff_locked"] is True
