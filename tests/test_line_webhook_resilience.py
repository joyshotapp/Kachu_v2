from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from pydantic import ValidationError

from kachu.config import Settings
from kachu.document_parser import ParseResult
from kachu.line import webhook as line_webhook_module
from kachu.models import BossRouteDecision, BossRouteMode, Intent


@pytest.mark.asyncio
async def test_download_line_content_retries_until_success() -> None:
    with patch(
        "kachu.line.webhook._download_line_image",
        new=AsyncMock(side_effect=[
            httpx.ReadTimeout("slow"),
            httpx.ReadTimeout("slow-again"),
            b"image-bytes",
        ]),
    ) as download_mock, patch(
        "kachu.line.webhook.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        content = await line_webhook_module._download_line_content_with_retry(
            "msg-1",
            "token",
        )

    assert content == b"image-bytes"
    assert download_mock.await_count == 3
    assert sleep_mock.await_count == 2


@pytest.mark.asyncio
async def test_download_line_content_stops_on_non_retriable_error() -> None:
    request = httpx.Request("GET", "https://api-data.line.me")
    response = httpx.Response(404, request=request)
    error = httpx.HTTPStatusError("not found", request=request, response=response)

    with patch(
        "kachu.line.webhook._download_line_image",
        new=AsyncMock(side_effect=error),
    ) as download_mock, patch(
        "kachu.line.webhook.asyncio.sleep",
        new=AsyncMock(),
    ) as sleep_mock:
        with pytest.raises(httpx.HTTPStatusError):
            await line_webhook_module._download_line_content_with_retry("msg-1", "token")

    assert download_mock.await_count == 1
    assert sleep_mock.await_count == 0

def test_parse_schedule_datetime_defaults_to_hour_when_minute_missing() -> None:
    now_local = datetime.fromisoformat("2026-05-02T12:00:00+08:00")

    scheduled_for, error_text = line_webhook_module._parse_schedule_datetime(
        "5月3日晚上8點",
        now_local=now_local,
    )

    assert error_text is None
    assert scheduled_for is not None
    assert scheduled_for.month == 5
    assert scheduled_for.day == 3
    assert scheduled_for.hour == 20
    assert scheduled_for.minute == 0


@pytest.mark.asyncio
async def test_schedule_publish_backfills_preview_image_url_for_photo_content() -> None:
    repo = MagicMock()
    repo.get_pending_approval_by_run_id.return_value = SimpleNamespace(
        workflow_type="kachu_photo_content",
        draft_content='{"ig_fb": "測試 IG 草稿", "google": "測試 Google 商家草稿"}',
    )

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ):
        await line_webhook_module._handle_event(
            event={
                "type": "postback",
                "source": {"userId": "U123"},
                "postback": {"data": "action=schedule_publish&run_id=run-photo-1&tenant_id=U123"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=AsyncMock(),
            onboarding_flow=AsyncMock(),
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
                KACHU_BASE_URL="https://app.kachu.tw",
            ),
        )

    saved_content = repo.save_shared_context.call_args.kwargs["content"]
    assert saved_content["draft_content"]["image_url"].endswith(
        "/tools/approval-photo/run-photo-1"
    )


@pytest.mark.asyncio
async def test_notify_processing_failure_uses_push_message_api() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        LINE_CHANNEL_ACCESS_TOKEN="token",
    )

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._notify_processing_failure(
            line_user_id="U123",
            settings=settings,
            text="圖片下載失敗，請重新上傳。",
        )

    push_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_control_handles_recoverable_retry_error() -> None:
    agentos = AsyncMock()
    agentos.retry_run.side_effect = httpx.ReadTimeout("timeout")

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "postback",
                "source": {"userId": "U123"},
                "postback": {"data": "action=retry_run&run_id=run-1"},
            },
            repo=AsyncMock(),
            agentOS_client=agentos,
            approval_bridge=AsyncMock(),
            intent_router=AsyncMock(),
            onboarding_flow=AsyncMock(),
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
        )

    push_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_runtime_control_unexpected_retry_error_bubbles_up() -> None:
    agentos = AsyncMock()
    agentos.retry_run.side_effect = AssertionError("unexpected retry bug")

    with pytest.raises(AssertionError, match="unexpected retry bug"):
        await line_webhook_module._handle_event(
            event={
                "type": "postback",
                "source": {"userId": "U123"},
                "postback": {"data": "action=retry_run&run_id=run-1"},
            },
            repo=AsyncMock(),
            agentOS_client=agentos,
            approval_bridge=AsyncMock(),
            intent_router=AsyncMock(),
            onboarding_flow=AsyncMock(),
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
        )


@pytest.mark.asyncio
async def test_completed_onboarding_image_can_be_absorbed_as_brand_knowledge() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    memory_manager = AsyncMock()
    context_brief_manager = AsyncMock()
    intent_router = AsyncMock()

    with patch(
        "kachu.line.webhook._download_line_content_with_retry",
        new=AsyncMock(return_value=b"image-bytes"),
    ), patch(
        "kachu.line.webhook.document_parser.parse_document",
        new=AsyncMock(
            return_value=ParseResult(
                text="【圖片分析】品牌故事與產品主打",
                source_type="image_parsed",
                confidence=0.95,
            )
        ),
    ), patch(
        "kachu.line.webhook._classify_media_after_onboarding",
        new=AsyncMock(return_value=("knowledge", "品牌故事與產品主打", "test")),
    ), patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "replyToken": "reply-1",
                "source": {"userId": "U123"},
                "message": {"type": "image", "id": "img-1"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=memory_manager,
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=context_brief_manager,
            business_consultant=AsyncMock(),
        )

    memory_manager.store_knowledge.assert_awaited_once()
    intent_router.dispatch.assert_not_awaited()
    context_brief_manager.refresh_briefs.assert_awaited_once()
    push_mock.assert_awaited_once()
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert any("品牌資料" in message.get("text", "") for message in sent_messages)


@pytest.mark.asyncio
async def test_completed_onboarding_image_can_trigger_clarification() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False

    with patch(
        "kachu.line.webhook._download_line_content_with_retry",
        new=AsyncMock(return_value=b"image-bytes"),
    ), patch(
        "kachu.line.webhook.document_parser.parse_document",
        new=AsyncMock(
            return_value=ParseResult(
                text="【圖片分析】產品視覺與簡短標題",
                source_type="image_parsed",
                confidence=0.8,
            )
        ),
    ), patch(
        "kachu.line.webhook._classify_media_after_onboarding",
        new=AsyncMock(return_value=("clarify", "產品視覺與簡短標題", "ambiguous")),
    ), patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "replyToken": "reply-1",
                "source": {"userId": "U123"},
                "message": {"type": "image", "id": "img-1"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=AsyncMock(),
            onboarding_flow=onboarding_flow,
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=AsyncMock(),
            business_consultant=AsyncMock(),
        )

    repo.save_shared_context.assert_called_once()
    sent_messages = push_mock.await_args.kwargs["messages"]
    # new UX: postback quick-reply buttons instead of plain text choices
    msg = sent_messages[0]
    quick_items = msg.get("quickReply", {}).get("items", [])
    actions_data = [item["action"]["data"] for item in quick_items]
    assert any("decision=photo_content" in d for d in actions_data)
    assert any("decision=knowledge" in d for d in actions_data)
    assert any("decision=consult" in d for d in actions_data)


@pytest.mark.asyncio
async def test_pending_asset_reply_brand_data_routes_to_knowledge_capture() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = {
        "summary": "產品說明卡",
        "knowledge_text": "品牌故事與產品主打",
        "source_type": "image_parsed",
        "source_id": "img-1",
        "line_message_id": "img-1",
        "photo_url": "data:image/jpeg;base64,abc",
        "reply_token": "reply-1",
    }
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    memory_manager = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "這是品牌資料，幫我記住"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=AsyncMock(),
            onboarding_flow=onboarding_flow,
            memory_manager=memory_manager,
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=AsyncMock(),
            business_consultant=AsyncMock(),
        )

    memory_manager.store_knowledge.assert_awaited_once()
    repo.save_shared_context.assert_called_once()
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert any("品牌資料" in message.get("text", "") for message in sent_messages)


@pytest.mark.asyncio
async def test_explicit_brand_text_is_absorbed_without_workflow_dispatch() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    memory_manager = AsyncMock()
    intent_router = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "這是品牌資訊：我們主打草本濃縮與日常調理"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=memory_manager,
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=AsyncMock(),
            business_consultant=AsyncMock(),
        )

    memory_manager.store_knowledge.assert_awaited_once()
    intent_router.dispatch.assert_not_awaited()
    push_mock.assert_awaited_once()


def test_target_audience_statement_without_explicit_prefix_is_not_absorbed() -> None:
    assert line_webhook_module._should_absorb_explicit_knowledge_text("目標客群鎖定 30-45 歲女性") is False


@pytest.mark.asyncio
async def test_brand_keyword_question_routes_to_consultation_instead_of_knowledge_absorption() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    memory_manager = AsyncMock()
    intent_router = AsyncMock()
    intent_router.plan_boss_message = AsyncMock(return_value=BossRouteDecision(
        mode=BossRouteMode.CONSULT,
        intent=Intent.KNOWLEDGE_UPDATE,
        topic="",
        actions=[{"label": "幫我整理客群", "intent": "knowledge_update", "topic": "客群"}],
    ))
    consultant = AsyncMock()
    consultant.build_reply = AsyncMock(return_value={"type": "text", "text": "我會先幫你釐清目標客群，再反推訊息與投放。"})

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "那你覺得目標客群要怎麼設定？"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=memory_manager,
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=AsyncMock(),
            business_consultant=consultant,
        )

    memory_manager.store_knowledge.assert_not_awaited()
    consultant.build_reply.assert_awaited_once()
    intent_router.dispatch.assert_not_awaited()
    assert repo.save_conversation.call_args_list[0].kwargs["conversation_type"] == "consultation"
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert sent_messages[0]["text"] == "我會先幫你釐清目標客群，再反推訊息與投放。"


@pytest.mark.asyncio
async def test_misclassified_report_question_defaults_to_consultation() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, None]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()
    intent_router.plan_boss_message = AsyncMock(return_value=BossRouteDecision(
        mode=BossRouteMode.CONSULT,
        intent=Intent.GA4_REPORT,
        topic="",
        actions=[{"label": "幫我拉一份流量報告", "intent": "ga4_report", "topic": ""}],
    ))
    context_brief_manager = AsyncMock()
    consultant = AsyncMock()
    consultant.build_reply = AsyncMock(return_value={"type": "text", "text": "先別急著拉報表，我會先從流量下滑的來源與最近變化幫你拆原因。"})

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "最近流量掉很多，我想先理解問題在哪"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=context_brief_manager,
            business_consultant=consultant,
        )

    repo.save_shared_context.assert_not_called()
    assert repo.save_conversation.call_count == 2
    assert repo.save_conversation.call_args_list[0].kwargs["conversation_type"] == "consultation"
    assert repo.save_conversation.call_args_list[1].kwargs["conversation_type"] == "consultation"
    context_brief_manager.refresh_briefs.assert_not_awaited()
    intent_router.dispatch.assert_not_awaited()
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert sent_messages[0]["text"] == "先別急著拉報表，我會先從流量下滑的來源與最近變化幫你拆原因。"


@pytest.mark.asyncio
async def test_ambiguous_report_statement_still_prompts_before_dispatch() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, None]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()
    intent_router.plan_boss_message = AsyncMock(return_value=BossRouteDecision(
        mode=BossRouteMode.CLARIFY,
        intent=Intent.GA4_REPORT,
        topic="",
        actions=[],
        clarify_question="你說流量掉很多，是要我拉報告看數字，還是先討論原因？",
    ))
    context_brief_manager = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "最近流量掉很多"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=context_brief_manager,
            business_consultant=AsyncMock(),
        )

    repo.save_shared_context.assert_called_once()
    repo.save_conversation.assert_called_once()
    assert repo.save_conversation.call_args.kwargs["conversation_type"] == "consultation"
    context_brief_manager.refresh_briefs.assert_not_awaited()
    intent_router.dispatch.assert_not_awaited()
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert sent_messages[0]["type"] == "text"
    assert "流量" in sent_messages[0]["text"] or "報告" in sent_messages[0]["text"] or "討論" in sent_messages[0]["text"]
    assert "quickReply" not in sent_messages[0]


@pytest.mark.asyncio
async def test_consultation_question_is_saved_separately_from_commands() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, None]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()
    intent_router.plan_boss_message = AsyncMock(return_value=BossRouteDecision(
        mode=BossRouteMode.CONSULT,
        intent=Intent.GENERAL_CHAT,
        topic="",
        actions=[],
    ))
    consultant = AsyncMock()
    consultant.build_reply = AsyncMock(return_value={"type": "text", "text": "我會先看你的產品差異化與市場切入點。"})
    context_brief_manager = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ):
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "你對我的產品和市場有什麼看法？"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=context_brief_manager,
            business_consultant=consultant,
        )

    assert repo.save_conversation.call_count == 2
    assert repo.save_conversation.call_args_list[0].kwargs["conversation_type"] == "consultation"
    assert repo.save_conversation.call_args_list[1].kwargs["conversation_type"] == "consultation"
    context_brief_manager.refresh_briefs.assert_not_awaited()


@pytest.mark.asyncio
async def test_general_chat_small_talk_skips_business_consultant() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, None]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()
    intent_router.plan_boss_message = AsyncMock(return_value=BossRouteDecision(
        mode=BossRouteMode.CONSULT,
        intent=Intent.GENERAL_CHAT,
        topic="",
        actions=[],
        small_talk=True,
    ))
    consultant = AsyncMock()
    consultant.build_reply = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "你好"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=AsyncMock(),
            business_consultant=consultant,
        )

    consultant.build_reply.assert_not_awaited()
    assert repo.save_conversation.call_count == 2
    assert repo.save_conversation.call_args_list[0].kwargs["conversation_type"] == "general"
    assert repo.save_conversation.call_args_list[1].kwargs["conversation_type"] == "general"
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert sent_messages[0]["type"] == "text"
    assert sent_messages[0]["text"].startswith("你好，我在。")


@pytest.mark.asyncio
async def test_pending_text_intent_can_fall_back_to_consultation() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, {
        "intent": str(line_webhook_module.Intent.GA4_REPORT),
        "message": "最近流量掉很多，我想先理解問題在哪",
        "topic": "",
    }]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    consultant = AsyncMock()
    consultant.build_reply = AsyncMock(return_value={"type": "text", "text": "先一起看問題脈絡。"})
    intent_router = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "message",
                "source": {"userId": "U123"},
                "message": {"type": "text", "text": "先討論"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=intent_router,
            onboarding_flow=onboarding_flow,
            memory_manager=AsyncMock(),
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=AsyncMock(),
            business_consultant=consultant,
        )

    intent_router.dispatch.assert_not_awaited()
    consultant.build_reply.assert_awaited_once()
    repo.save_shared_context.assert_called_once()
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert sent_messages[0]["text"] == "先一起看問題脈絡。"


@pytest.mark.asyncio
async def test_pending_text_intent_manual_execute_reply_dispatches() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, {
        "intent": str(line_webhook_module.Intent.GA4_REPORT),
        "message": "最近流量掉很多",
        "topic": "流量下降",
        "actions": [{"label": "幫我拉一份流量報告", "intent": "ga4_report", "topic": "流量下降"}],
    }]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()

    await line_webhook_module._handle_event(
        event={
            "type": "message",
            "source": {"userId": "U123"},
            "message": {"id": "msg-123", "type": "text", "text": "幫我拉一份流量報告"},
        },
        repo=repo,
        agentOS_client=AsyncMock(),
        approval_bridge=AsyncMock(),
        intent_router=intent_router,
        onboarding_flow=onboarding_flow,
        memory_manager=AsyncMock(),
        settings=Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            LINE_CHANNEL_ACCESS_TOKEN="token",
            LINE_BOSS_USER_ID="U123",
        ),
        context_brief_manager=AsyncMock(),
        business_consultant=AsyncMock(),
    )

    intent_router.dispatch.assert_awaited_once()
    assert intent_router.dispatch.await_args.kwargs["intent"] == line_webhook_module.Intent.GA4_REPORT
    assert intent_router.dispatch.await_args.kwargs["trigger_payload"]["line_message_id"] == "msg-123"


def test_pending_asset_reply_prioritizes_consult_over_brand_keyword() -> None:
    assert line_webhook_module._resolve_pending_asset_reply("先一起討論品牌方向") == "consult"


@pytest.mark.asyncio
async def test_explicit_review_execution_still_dispatches_immediately() -> None:
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    repo.get_shared_context.side_effect = [None, None]
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()
    intent_router.plan_boss_message = AsyncMock(return_value=BossRouteDecision(
        mode=BossRouteMode.EXECUTE,
        intent=Intent.REVIEW_REPLY,
        topic="",
        actions=[],
    ))

    await line_webhook_module._handle_event(
        event={
            "type": "message",
            "source": {"userId": "U123"},
            "message": {"id": "review-msg-1", "type": "text", "text": "幫我回覆評論"},
        },
        repo=repo,
        agentOS_client=AsyncMock(),
        approval_bridge=AsyncMock(),
        intent_router=intent_router,
        onboarding_flow=onboarding_flow,
        memory_manager=AsyncMock(),
        settings=Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            LINE_CHANNEL_ACCESS_TOKEN="token",
            LINE_BOSS_USER_ID="U123",
        ),
        context_brief_manager=AsyncMock(),
        business_consultant=AsyncMock(),
    )

    intent_router.dispatch.assert_awaited_once()
    repo.save_shared_context.assert_not_called()
    assert intent_router.dispatch.await_args.kwargs["trigger_payload"]["line_message_id"] == "review-msg-1"


# ── asset_intent postback handler ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_asset_intent_postback_photo_content_dispatches_workflow() -> None:
    """postback action=asset_intent&decision=photo_content → PHOTO_CONTENT dispatch"""
    pending_ctx = {
        "line_message_id": "img-99",
        "photo_url": "data:image/jpeg;base64,abc",
        "knowledge_text": "疏通飲產品圖",
        "source_type": "image_parsed",
        "source_id": "img-99",
        "summary": "疏通飲",
        "reply_token": "",
    }
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = pending_ctx
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()

    await line_webhook_module._handle_event(
        event={
            "type": "postback",
            "source": {"userId": "U123"},
            "postback": {"data": "action=asset_intent&decision=photo_content"},
        },
        repo=repo,
        agentOS_client=AsyncMock(),
        approval_bridge=AsyncMock(),
        intent_router=intent_router,
        onboarding_flow=onboarding_flow,
        memory_manager=AsyncMock(),
        settings=Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            LINE_CHANNEL_ACCESS_TOKEN="token",
            LINE_BOSS_USER_ID="U123",
        ),
        context_brief_manager=AsyncMock(),
        business_consultant=AsyncMock(),
    )

    intent_router.dispatch.assert_awaited_once()
    call_kwargs = intent_router.dispatch.await_args.kwargs
    assert call_kwargs["intent"] == Intent.PHOTO_CONTENT
    assert call_kwargs["trigger_payload"]["photo_url"] == "data:image/jpeg;base64,abc"
    # _clear_pending_asset_context marks context resolved via save_shared_context
    repo.save_shared_context.assert_called_once()


@pytest.mark.asyncio
async def test_asset_intent_postback_knowledge_stores_to_kb() -> None:
    """postback action=asset_intent&decision=knowledge → knowledge capture"""
    pending_ctx = {
        "line_message_id": "img-99",
        "photo_url": "data:image/jpeg;base64,abc",
        "knowledge_text": "疏通飲成分：山楂、決明子",
        "source_type": "image_parsed",
        "source_id": "img-99",
        "summary": "疏通飲成分",
        "reply_token": "",
    }
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = pending_ctx
    repo.get_or_create_tenant.return_value = SimpleNamespace(name="四時循養堂", industry_type="保健")
    repo.get_knowledge_entries.return_value = []
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    memory_manager = AsyncMock()
    context_brief_manager = AsyncMock()

    with patch(
        "kachu.line.webhook.push_line_messages",
        new=AsyncMock(),
    ) as push_mock:
        await line_webhook_module._handle_event(
            event={
                "type": "postback",
                "source": {"userId": "U123"},
                "postback": {"data": "action=asset_intent&decision=knowledge"},
            },
            repo=repo,
            agentOS_client=AsyncMock(),
            approval_bridge=AsyncMock(),
            intent_router=AsyncMock(),
            onboarding_flow=onboarding_flow,
            memory_manager=memory_manager,
            settings=Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_BOSS_USER_ID="U123",
            ),
            context_brief_manager=context_brief_manager,
            business_consultant=AsyncMock(),
        )

    memory_manager.store_knowledge.assert_awaited_once()
    push_mock.assert_awaited_once()
    sent_messages = push_mock.await_args.kwargs["messages"]
    assert any("品牌資料" in m.get("text", "") for m in sent_messages)


@pytest.mark.asyncio
async def test_asset_intent_postback_no_pending_context_is_silent() -> None:
    """postback arrives but pending context already expired → no crash, no action"""
    repo = MagicMock()
    repo.get_active_edit_session.return_value = None
    repo.get_shared_context.return_value = None  # expired / already handled
    onboarding_flow = MagicMock()
    onboarding_flow.is_in_onboarding.return_value = False
    intent_router = AsyncMock()

    await line_webhook_module._handle_event(
        event={
            "type": "postback",
            "source": {"userId": "U123"},
            "postback": {"data": "action=asset_intent&decision=photo_content"},
        },
        repo=repo,
        agentOS_client=AsyncMock(),
        approval_bridge=AsyncMock(),
        intent_router=intent_router,
        onboarding_flow=onboarding_flow,
        memory_manager=AsyncMock(),
        settings=Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            LINE_CHANNEL_ACCESS_TOKEN="token",
            LINE_BOSS_USER_ID="U123",
        ),
        context_brief_manager=AsyncMock(),
        business_consultant=AsyncMock(),
    )

    intent_router.dispatch.assert_not_awaited()  # no action taken