"""WP-7: Dedicated Meta publish integration tests."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from kachu.config import Settings
from kachu.main import create_app
from kachu.meta.client import MetaAPIError
from kachu.persistence.tables import ConnectorAccountTable


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        LINE_CHANNEL_ACCESS_TOKEN="test-token",
        LINE_CHANNEL_SECRET="",
        LINE_BOSS_USER_ID="U_boss",
        AGENTOS_BASE_URL="http://agentos-mock",
        KACHU_BASE_URL="http://localhost:8001",
        DATABASE_URL="sqlite://",
    )


@pytest.fixture()
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings))


def _connect_meta_account(client: TestClient, tenant_id: str) -> None:
    repo = client.app.state.repository
    creds = json.dumps(
        {
            "access_token": "meta-token",
            "ig_user_id": "ig-user-123",
            "fb_page_id": "fb-page-456",
        }
    )
    with Session(repo._engine) as session:
        session.add(
            ConnectorAccountTable(
                tenant_id=tenant_id,
                platform="meta",
                credentials_encrypted=creds,
                account_label="Test Meta",
                is_active=True,
            )
        )
        session.commit()


def test_publish_content_ig_fb_no_credentials(client: TestClient) -> None:
    resp = client.post(
        "/tools/publish-content",
        json={
            "tenant_id": "tenant-no-meta",
            "run_id": "run-meta-001",
            "selected_platforms": ["ig_fb"],
            "drafts": {"ig_fb": "Test IG caption"},
        },
    )

    assert resp.status_code == 200
    assert resp.json()["results"]["ig_fb"]["status"] == "skipped_no_credentials"


def test_publish_content_ig_fb_photo_success(client: TestClient) -> None:
    _connect_meta_account(client, "tenant-meta-photo")

    with patch(
        "kachu.meta.client.MetaClient.post_ig_photo",
        new=AsyncMock(return_value={"creation_id": "c1", "ig_media_id": "m1"}),
    ) as mock_ig, patch(
        "kachu.meta.client.MetaClient.post_fb_photo",
        new=AsyncMock(return_value={"fb_post_id": "p1"}),
    ) as mock_fb:
        resp = client.post(
            "/tools/publish-content",
            json={
                "tenant_id": "tenant-meta-photo",
                "run_id": "run-meta-002",
                "selected_platforms": ["ig_fb"],
                "drafts": {
                    "ig_fb": "Photo caption",
                    "image_url": "https://example.com/photo.jpg",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()["results"]["ig_fb"]
    assert data["status"] == "done"
    assert data["instagram"]["status"] == "published"
    assert data["facebook"]["status"] == "published"
    mock_ig.assert_awaited_once()
    mock_fb.assert_awaited_once()


def test_publish_content_ig_fb_text_only_falls_back_to_facebook(client: TestClient) -> None:
    _connect_meta_account(client, "tenant-meta-text")

    with patch(
        "kachu.meta.client.MetaClient.post_fb_text",
        new=AsyncMock(return_value={"fb_post_id": "fb-text-001"}),
    ) as mock_fb_text:
        resp = client.post(
            "/tools/publish-content",
            json={
                "tenant_id": "tenant-meta-text",
                "run_id": "run-meta-003",
                "selected_platforms": ["ig_fb"],
                "drafts": {"ig_fb": "Text only caption"},
            },
        )

    assert resp.status_code == 200
    data = resp.json()["results"]["ig_fb"]
    assert data["status"] == "done"
    assert data["instagram"]["status"] == "skipped"
    assert data["facebook"]["status"] == "published"
    mock_fb_text.assert_awaited_once()


def test_publish_content_ig_fb_meta_failure_isolated(client: TestClient) -> None:
    _connect_meta_account(client, "tenant-meta-fail")

    with patch(
        "kachu.meta.client.MetaClient.post_ig_photo",
        new=AsyncMock(side_effect=MetaAPIError("expired token", 401)),
    ):
        resp = client.post(
            "/tools/publish-content",
            json={
                "tenant_id": "tenant-meta-fail",
                "run_id": "run-meta-004",
                "selected_platforms": ["ig_fb"],
                "drafts": {
                    "ig_fb": "Broken token caption",
                    "image_url": "https://example.com/photo.jpg",
                },
            },
        )

    assert resp.status_code == 200
    data = resp.json()["results"]["ig_fb"]
    assert data["status"] == "failed"
    assert "expired token" in data["error"]


def test_publish_content_ig_fb_unexpected_error_is_not_swallowed(client: TestClient) -> None:
    _connect_meta_account(client, "tenant-meta-bug")

    with patch(
        "kachu.meta.client.MetaClient.post_ig_photo",
        new=AsyncMock(side_effect=AssertionError("unexpected bug")),
    ):
        with pytest.raises(AssertionError, match="unexpected bug"):
            client.post(
                "/tools/publish-content",
                json={
                    "tenant_id": "tenant-meta-bug",
                    "run_id": "run-meta-005",
                    "selected_platforms": ["ig_fb"],
                    "drafts": {
                        "ig_fb": "Broken caption",
                        "image_url": "https://example.com/photo.jpg",
                    },
                },
            )