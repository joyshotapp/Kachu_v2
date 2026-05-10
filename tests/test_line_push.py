from __future__ import annotations

from unittest.mock import patch

import pytest

from kachu_plus.config import Settings
from kachu_plus.line import push as push_module


class _FakeResponse:
    def raise_for_status(self) -> None:
        return None


class _FakeAsyncClient:
    calls: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url: str, *, headers: dict, content: bytes, timeout: float):
        self.calls.append({"url": url, "headers": headers, "content": content, "timeout": timeout})
        return _FakeResponse()


@pytest.mark.asyncio
async def test_push_line_messages_applies_min_interval_throttle() -> None:
    push_module._PUSH_LOCKS.clear()
    push_module._LAST_PUSH_COMPLETED_AT.clear()
    _FakeAsyncClient.calls = []
    sleep_calls: list[float] = []
    monotonic_values = iter([0.0, 0.1, 0.2, 0.6])

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(round(delay, 4))

    with patch.object(push_module, "get_settings", return_value=Settings(LINE_PUSH_MIN_INTERVAL_SECONDS=0.5)), \
        patch.object(push_module.httpx, "AsyncClient", return_value=_FakeAsyncClient()), \
        patch.object(push_module.asyncio, "sleep", side_effect=_fake_sleep), \
        patch.object(push_module.time, "monotonic", side_effect=lambda: next(monotonic_values)):
        await push_module.push_line_messages(
            to="U1",
            messages=[{"type": "text", "text": "first"}],
            access_token="token-1",
        )
        await push_module.push_line_messages(
            to="U1",
            messages=[{"type": "text", "text": "second"}],
            access_token="token-1",
        )

    assert len(_FakeAsyncClient.calls) == 2
    assert sleep_calls == [0.4]
