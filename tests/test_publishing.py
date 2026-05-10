from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from kachu_plus.google_business import GoogleBusinessConnectorError
from kachu_plus.meta import MetaConnectorError
from kachu_plus.publishing import (
    _LAST_PUBLISH_COMPLETED_AT,
    _PUBLISH_LOCKS,
    publish_content_bundle,
    publish_meta_reply,
    publish_review_reply,
)


class _FakeGBPClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def post_reply(self, account_id: str, location_id: str, review_id: str, reply_text: str):
        self.calls.append((review_id, reply_text))
        return {"reviewId": review_id, "comment": reply_text}


class _FakeMetaClient:
    def __init__(self, *, access_token: str = "", fb_page_id: str = "", ig_user_id: str = "", fb_access_token: str = "") -> None:
        self.fb_page_id = fb_page_id
        self.calls: list[tuple[str, str]] = []

    def send_page_message(self, *, recipient_id: str, message: str):
        self.calls.append((recipient_id, message))
        return {"message_id": f"msg:{recipient_id}"}

    def post_ig_text(self, *, caption: str):
        self.calls.append(("ig", caption))
        return {"status": "skipped", "reason": "instagram_text_only_not_supported", "caption": caption}

    def post_fb_text(self, *, message: str):
        self.calls.append(("fb", message))
        return {"fb_post_id": "fb-post-1"}


def setup_function() -> None:
    _PUBLISH_LOCKS.clear()
    _LAST_PUBLISH_COMPLETED_AT.clear()


def test_publish_review_reply_applies_google_business_throttle() -> None:
    repo = MagicMock()
    fake_client = _FakeGBPClient()
    review_service = MagicMock()
    review_service.resolve_client_context.return_value = (fake_client, "acct-1", "loc-1")
    settings = SimpleNamespace(GOOGLE_BUSINESS_MIN_INTERVAL_SECONDS=2.0, META_GRAPH_MIN_INTERVAL_SECONDS=0.0)

    with patch("kachu_plus.publishing.get_settings", return_value=settings), patch(
        "kachu_plus.publishing.time.monotonic",
        side_effect=[0.0, 0.5, 2.1],
    ), patch("kachu_plus.publishing.time.sleep") as mock_sleep:
        first = publish_review_reply(
            repo=repo,
            review_service=review_service,
            tenant_id="tenant-1",
            run_id="run-1",
            review_id="review-1",
            reply_text="謝謝你",
        )
        second = publish_review_reply(
            repo=repo,
            review_service=review_service,
            tenant_id="tenant-1",
            run_id="run-2",
            review_id="review-2",
            reply_text="歡迎再來",
        )

    assert first["status"] == "posted"
    assert second["status"] == "posted"
    mock_sleep.assert_called_once_with(1.5)


def test_publish_meta_reply_applies_meta_throttle() -> None:
    repo = MagicMock()
    repo.get_connector_account.return_value = SimpleNamespace(
        credentials_json='{"access_token":"meta-token","fb_page_id":"fb-page-1","fb_access_token":"page-token"}'
    )
    engagement = SimpleNamespace(
        engagement_type="message",
        author_id="user-1",
        external_thread_id="thread-1",
        external_message_id="message-1",
    )
    settings = SimpleNamespace(GOOGLE_BUSINESS_MIN_INTERVAL_SECONDS=0.0, META_GRAPH_MIN_INTERVAL_SECONDS=1.0)

    with patch("kachu_plus.publishing.get_settings", return_value=settings), patch(
        "kachu_plus.publishing.resolve_meta_graph_client",
        side_effect=[
            (_FakeMetaClient(fb_page_id="fb-page-1"), {"fb_page_id": "fb-page-1"}),
            (_FakeMetaClient(fb_page_id="fb-page-1"), {"fb_page_id": "fb-page-1"}),
        ],
    ), patch(
        "kachu_plus.publishing.time.monotonic",
        side_effect=[0.0, 0.25, 1.3],
    ), patch("kachu_plus.publishing.time.sleep") as mock_sleep:
        first = publish_meta_reply(
            repo=repo,
            tenant_id="tenant-1",
            run_id="run-1",
            engagement=engagement,
            reply_text="第一則回覆",
        )
        second = publish_meta_reply(
            repo=repo,
            tenant_id="tenant-1",
            run_id="run-2",
            engagement=engagement,
            reply_text="第二則回覆",
        )

    assert first["status"] == "posted"
    assert second["status"] == "posted"
    mock_sleep.assert_called_once_with(0.75)


def test_publish_review_reply_returns_failed_when_google_connector_expired() -> None:
    repo = MagicMock()
    review_service = MagicMock()
    review_service.resolve_client_context.side_effect = GoogleBusinessConnectorError("google_business credential expired and no refresh token is available")

    result = publish_review_reply(
        repo=repo,
        review_service=review_service,
        tenant_id="tenant-1",
        run_id="run-1",
        review_id="review-1",
        reply_text="謝謝你",
    )

    assert result["status"] == "failed"
    assert "expired" in result["payload"]["error"]
    repo.record_published_content.assert_not_called()


def test_publish_meta_reply_returns_failed_when_meta_connector_expired() -> None:
    repo = MagicMock()
    engagement = SimpleNamespace(
        engagement_type="message",
        author_id="user-1",
        external_thread_id="thread-1",
        external_message_id="message-1",
    )

    with patch(
        "kachu_plus.publishing.resolve_meta_graph_client",
        side_effect=MetaConnectorError("meta connector credential expired"),
    ):
        result = publish_meta_reply(
            repo=repo,
            tenant_id="tenant-1",
            run_id="run-1",
            engagement=engagement,
            reply_text="第一則回覆",
        )

    assert result["status"] == "failed"
    assert "expired" in result["error"]
    repo.record_published_content.assert_not_called()


def test_publish_content_bundle_returns_failed_when_meta_connector_expired() -> None:
    repo = MagicMock()
    review_service = MagicMock()

    with patch(
        "kachu_plus.publishing.resolve_meta_graph_client",
        side_effect=MetaConnectorError("meta connector credential expired"),
    ):
        result = publish_content_bundle(
            repo=repo,
            review_service=review_service,
            tenant_id="tenant-1",
            run_id="run-1",
            drafts={"ig_fb": "這是一篇貼文"},
            selected_platforms=["ig_fb"],
        )

    assert result["ig_fb"]["status"] == "failed"
    assert "expired" in result["ig_fb"]["error"]
    repo.get_connector_account.assert_not_called()
    repo.record_published_content.assert_not_called()