from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from pydantic import ValidationError

from kachu.config import Settings
from kachu.line import webhook as line_webhook_module


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