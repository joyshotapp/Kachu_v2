"""
WP-7: Onboarding document ingestion tests.

Verifies that:
1. Image uploads during awaiting_docs state trigger Gemini Vision parsing.
2. When GOOGLE_AI_API_KEY is missing, falls back to placeholder record.
3. File uploads trigger LlamaParse when LLAMAPARSE_API_KEY is available.
4. Text submitted during awaiting_docs is recorded as knowledge entry.
5. Skip keywords advance state to interview_q1.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from kachu.config import Settings
from kachu.onboarding.flow import OnboardingFlow
from kachu.persistence.tables import KnowledgeEntryTable, OnboardingStateTable, TenantTable


# ── Minimal in-memory repo stub ───────────────────────────────────────────────

class _StubRepo:
    def __init__(self):
        self._state: dict[str, str] = {}
        self.knowledge_entries: list[dict] = []

    def get_or_create_tenant(self, tenant_id: str) -> TenantTable:
        t = TenantTable(tenant_id=tenant_id)
        t.id = tenant_id
        return t

    def get_onboarding_state(self, tenant_id: str) -> OnboardingStateTable | None:
        step = self._state.get(tenant_id)
        if step is None:
            return None
        s = OnboardingStateTable(tenant_id=tenant_id)
        s.step = step
        return s

    def get_or_create_onboarding_state(self, tenant_id: str) -> OnboardingStateTable:
        state = self.get_onboarding_state(tenant_id)
        if state is None:
            state = OnboardingStateTable(tenant_id=tenant_id)
            state.step = "awaiting_docs"
            self._state[tenant_id] = "awaiting_docs"
        return state

    def update_onboarding_state(self, tenant_id: str, step: str) -> None:
        self._state[tenant_id] = step

    def save_knowledge_entry(self, *, tenant_id, category, content, source_type, source_id=None) -> None:
        self.knowledge_entries.append({
            "tenant_id": tenant_id,
            "category": category,
            "content": content,
            "source_type": source_type,
            "source_id": source_id,
        })


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_skip_keyword_advances_to_interview() -> None:
    repo = _StubRepo()
    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="", LLAMAPARSE_API_KEY="")
    flow = OnboardingFlow(repo, settings)

    messages = await flow.handle_message("t1", "text", "完成")
    assert repo._state["t1"] == "interview_q1"
    assert any("第 1 題" in m.get("text", "") for m in messages)


@pytest.mark.asyncio
async def test_text_during_awaiting_docs_saved_as_knowledge() -> None:
    repo = _StubRepo()
    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="", LLAMAPARSE_API_KEY="")
    flow = OnboardingFlow(repo, settings)

    await flow.handle_message("t1", "text", "我們的招牌是蜜糖吐司")
    assert len(repo.knowledge_entries) == 1
    entry = repo.knowledge_entries[0]
    assert entry["category"] == "document"
    assert "蜜糖吐司" in entry["content"]


@pytest.mark.asyncio
async def test_image_with_api_key_calls_gemini() -> None:
    """When GOOGLE_AI_API_KEY is set and bytes provided, Gemini Vision is called."""
    from kachu.document_parser import ParseResult

    repo = _StubRepo()
    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="test-key", LLAMAPARSE_API_KEY="")
    flow = OnboardingFlow(repo, settings)

    mock_result = ParseResult(
        text="【圖片分析】這是一張菜單，包含蜜糖吐司 $120",
        source_type="image_parsed",
        confidence=0.9,
    )
    with patch("kachu.document_parser.parse_document", new=AsyncMock(return_value=mock_result)):
        await flow.handle_message(
            "t1", "image", "msg-id-001",
            content_bytes=b"fake-image-bytes",
            mime_type="image/jpeg",
        )

    assert len(repo.knowledge_entries) == 1
    entry = repo.knowledge_entries[0]
    assert entry["source_type"] == "image_parsed"
    assert "菜單" in entry["content"]


@pytest.mark.asyncio
async def test_image_no_api_key_falls_back_to_placeholder() -> None:
    """When GOOGLE_AI_API_KEY is empty, placeholder is stored without calling parser."""
    repo = _StubRepo()
    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="", LLAMAPARSE_API_KEY="")
    flow = OnboardingFlow(repo, settings)

    # No content_bytes → triggers fallback path (settings is set but no bytes)
    messages = await flow.handle_message("t1", "image", "msg-999", content_bytes=None)
    assert len(repo.knowledge_entries) == 1
    entry = repo.knowledge_entries[0]
    assert "msg-999" in entry["content"] or entry["source_type"] == "document"


@pytest.mark.asyncio
async def test_audio_returns_manual_review_message() -> None:
    """Audio messages trigger the 'needs manual review' path."""
    from kachu.document_parser import ParseResult

    repo = _StubRepo()
    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="key", LLAMAPARSE_API_KEY="")
    flow = OnboardingFlow(repo, settings)

    audio_result = ParseResult(text="", source_type="audio_stub", confidence=0.0, needs_manual=True)
    with patch("kachu.document_parser.parse_document", new=AsyncMock(return_value=audio_result)):
        messages = await flow.handle_message(
            "t1", "audio", "audio-msg-id",
            content_bytes=b"audio",
            mime_type="audio/m4a",
        )

    assert any("無法自動解析" in m.get("text", "") for m in messages)


@pytest.mark.asyncio
async def test_parse_file_polls_until_llamaparse_result_ready(monkeypatch) -> None:
    from kachu import document_parser

    sleep_calls: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    class _FakeResponse:
        def __init__(self, status_code: int, payload: dict[str, object]):
            self.status_code = status_code
            self._payload = payload

        def json(self) -> dict[str, object]:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                request = document_parser.httpx.Request("GET", "https://example.com")
                response = document_parser.httpx.Response(self.status_code, request=request)
                raise document_parser.httpx.HTTPStatusError(
                    "request failed",
                    request=request,
                    response=response,
                )

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.poll_count = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args, **kwargs):
            return _FakeResponse(200, {"id": "job-1"})

        async def get(self, *args, **kwargs):
            self.poll_count += 1
            if self.poll_count < 3:
                return _FakeResponse(202, {})
            return _FakeResponse(200, {"text": "解析完成"})

    monkeypatch.setattr(document_parser.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(document_parser.httpx, "AsyncClient", _FakeAsyncClient)

    result = await document_parser._parse_file(
        file_bytes=b"pdf-bytes",
        mime_type="application/pdf",
        llamaparse_api_key="test-key",
    )

    assert result.text == "解析完成"
    assert result.needs_manual is False
    assert sleep_calls == [2.0, 3.0, 4.5]


@pytest.mark.asyncio
async def test_parse_document_degrades_on_recoverable_parser_error() -> None:
    from kachu.document_parser import parse_document

    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="test-key", LLAMAPARSE_API_KEY="")

    with patch(
        "kachu.document_parser._parse_image",
        new=AsyncMock(side_effect=httpx.ReadTimeout("timeout")),
    ):
        result = await parse_document(
            msg_type="image",
            content_bytes=b"fake-image",
            content_text=None,
            mime_type="image/jpeg",
            settings=settings,
        )

    assert result.needs_manual is True
    assert result.source_type == "image"
    assert result.error == "timeout"


@pytest.mark.asyncio
async def test_parse_document_re_raises_unexpected_parser_bug() -> None:
    from kachu.document_parser import parse_document

    settings = Settings(DATABASE_URL="sqlite://", GOOGLE_AI_API_KEY="test-key", LLAMAPARSE_API_KEY="")

    with patch(
        "kachu.document_parser._parse_image",
        new=AsyncMock(side_effect=AssertionError("unexpected")),
    ):
        with pytest.raises(AssertionError, match="unexpected"):
            await parse_document(
                msg_type="image",
                content_bytes=b"fake-image",
                content_text=None,
                mime_type="image/jpeg",
                settings=settings,
            )
