from __future__ import annotations

import json
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
import pytest

from kachu.auth.oauth import _push_line_texts, _store_pending_state_memory
from kachu.config import Settings
from kachu.google.business_client import GoogleBusinessClient
from kachu.main import create_app


def test_google_business_client_oauth_discovery_normalizes_location_names() -> None:
    client = GoogleBusinessClient.from_oauth_token("oauth-token")

    with patch("httpx.request") as request_mock:
        accounts_response = MagicMock()
        accounts_response.status_code = 200
        accounts_response.headers = {}
        accounts_response.json.return_value = {
            "accounts": [{"name": "accounts/123456789", "accountName": "Test Account"}]
        }
        accounts_response.raise_for_status.return_value = None

        locations_response = MagicMock()
        locations_response.status_code = 200
        locations_response.headers = {}
        locations_response.json.return_value = {
            "locations": [{"name": "locations/987654321", "title": "鴻笙堂 陳老師"}]
        }
        locations_response.raise_for_status.return_value = None

        request_mock.side_effect = [accounts_response, locations_response]

        accounts = client.list_accounts()
        locations = client.list_locations("accounts/123456789")

    assert accounts[0]["name"] == "accounts/123456789"
    assert locations[0]["name"] == "accounts/123456789/locations/987654321"


def test_google_business_client_uses_full_resource_names_for_publish_requests() -> None:
    client = GoogleBusinessClient.from_oauth_token("oauth-token")

    with patch("httpx.request") as request_mock:
        response = MagicMock()
        response.status_code = 200
        response.headers = {}
        response.json.return_value = {"name": "localPosts/1"}
        response.raise_for_status.return_value = None
        request_mock.return_value = response

        client.create_local_post(
            account_id="accounts/123456789",
            location_id="accounts/123456789/locations/987654321",
            summary="測試最新動態",
        )

    assert request_mock.call_args.args[1] == (
        "https://mybusiness.googleapis.com/v4/"
        "accounts/123456789/locations/987654321/localPosts"
    )


def test_google_business_client_retries_once_after_rate_limit() -> None:
    client = GoogleBusinessClient.from_oauth_token("oauth-token")

    rate_limited = MagicMock()
    rate_limited.status_code = 429
    rate_limited.headers = {"Retry-After": "1"}

    success = MagicMock()
    success.status_code = 200
    success.headers = {}
    success.json.return_value = {
        "accounts": [{"name": "accounts/123456789", "accountName": "Test Account"}]
    }
    success.raise_for_status.return_value = None

    with patch("httpx.request", side_effect=[rate_limited, success]) as request_mock, patch(
        "kachu.google.business_client.time.sleep"
    ) as sleep_mock:
        accounts = client.list_accounts()

    assert accounts[0]["name"] == "accounts/123456789"
    sleep_mock.assert_called_once_with(1)
    assert request_mock.call_count == 2


def test_google_callback_persists_discovered_account_and_location_ids() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
        KACHU_BASE_URL="http://localhost:8001",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        LINE_CHANNEL_SECRET="",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)
    app.state.repository.create_tenant_membership(
        tenant_id="tenant-google-oauth",
        line_user_id="U-owner-google",
        role="owner",
    )

    _store_pending_state_memory(
        "state-123",
        {"tenant_id": "tenant-google-oauth", "platforms": ["gbp"]},
        600,
    )

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "oauth-token",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/business.manage",
        "token_type": "Bearer",
    }

    discovery_client = MagicMock()
    discovery_client.list_accounts.return_value = [
        {"name": "accounts/123456789", "accountName": "Test Account"}
    ]
    discovery_client.list_locations.return_value = [
        {"name": "accounts/123456789/locations/987654321", "title": "鴻笙堂 陳老師"}
    ]

    with patch("httpx.AsyncClient.post", return_value=token_response), patch(
        "kachu.google.business_client.GoogleBusinessClient.from_oauth_token",
        return_value=discovery_client,
    ), patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        response = client.get(
            "/auth/google/callback",
            params={"code": "oauth-code", "state": "state-123"},
        )

    assert response.status_code == 200
    assert "Google 商家授權已完成" in response.text
    assert "內部驗證流程" in response.text
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["to"] == "U-owner-google"
    pushed_messages = push_mock.await_args.kwargs["messages"]
    assert any("目前渠道狀態" in message["text"] for message in pushed_messages)

    repo = app.state.repository
    connector = repo.get_connector_account("tenant-google-oauth", "google_business")
    assert connector is not None
    credentials = json.loads(connector.credentials_encrypted)
    assert credentials["account_id"] == "accounts/123456789"
    assert credentials["location_id"] == "accounts/123456789/locations/987654321"
    assert credentials["location_title"] == "鴻笙堂 陳老師"


def test_google_callback_renders_location_selection_when_multiple_locations() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
        KACHU_BASE_URL="http://localhost:8001",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        LINE_CHANNEL_SECRET="",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)

    _store_pending_state_memory(
        "state-google-many",
        {"tenant_id": "tenant-google-many", "platforms": ["gbp"]},
        600,
    )

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "oauth-token",
        "refresh_token": "refresh-token",
        "expires_in": 3600,
        "scope": "https://www.googleapis.com/auth/business.manage",
        "token_type": "Bearer",
    }

    discovery_client = MagicMock()
    discovery_client.list_accounts.return_value = [
        {"name": "accounts/123456789", "accountName": "Test Account"}
    ]
    discovery_client.list_locations.return_value = [
        {"name": "accounts/123456789/locations/111", "title": "店家 A"},
        {"name": "accounts/123456789/locations/222", "title": "店家 B"},
    ]

    with patch("httpx.AsyncClient.post", return_value=token_response), patch(
        "kachu.google.business_client.GoogleBusinessClient.from_oauth_token",
        return_value=discovery_client,
    ), patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        response = client.get(
            "/auth/google/callback",
            params={"code": "oauth-code", "state": "state-google-many"},
        )

    assert response.status_code == 200
    assert "選擇要綁定的 Google 商家" in response.text
    assert "店家 A" in response.text
    assert "店家 B" in response.text
    push_mock.assert_not_awaited()

    repo = app.state.repository
    connector = repo.get_connector_account("tenant-google-many", "google_business")
    assert connector is None


def test_google_select_location_persists_chosen_location() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        GOOGLE_OAUTH_CLIENT_ID="client-id",
        GOOGLE_OAUTH_CLIENT_SECRET="client-secret",
        KACHU_BASE_URL="http://localhost:8001",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        LINE_CHANNEL_SECRET="",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)
    app.state.repository.create_tenant_membership(
        tenant_id="tenant-google-many",
        line_user_id="U-owner-google-many",
        role="owner",
    )

    _store_pending_state_memory(
        "google-selection-123",
        {
            "tenant_id": "tenant-google-many",
            "platforms": ["gbp"],
            "google_token_data": {
                "access_token": "oauth-token",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/business.manage",
                "token_type": "Bearer",
            },
            "google_locations": [
                {
                    "account_id": "accounts/123456789",
                    "account_name": "Test Account",
                    "location_id": "accounts/123456789/locations/222",
                    "location_title": "店家 B",
                    "store_code": "B-001",
                }
            ],
        },
        600,
    )

    with patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        response = client.get(
            "/auth/google/select-location",
            params={
                "selection_token": "google-selection-123",
                "location_id": "accounts/123456789/locations/222",
            },
        )

    assert response.status_code == 200
    assert "Google 商家授權已完成" in response.text
    assert "店家 B" in response.text
    push_mock.assert_awaited_once()
    pushed_messages = push_mock.await_args.kwargs["messages"]
    assert any("店家 B" in message["text"] for message in pushed_messages)

    repo = app.state.repository
    connector = repo.get_connector_account("tenant-google-many", "google_business")
    assert connector is not None
    credentials = json.loads(connector.credentials_encrypted)
    assert credentials["account_id"] == "accounts/123456789"
    assert credentials["location_id"] == "accounts/123456789/locations/222"
    assert credentials["location_title"] == "店家 B"


def test_meta_callback_persists_ids_and_returns_success_page() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        KACHU_BASE_URL="http://localhost:8001",
        META_APP_ID="meta-app-id",
        META_APP_SECRET="meta-app-secret",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)
    app.state.repository.create_tenant_membership(
        tenant_id="tenant-meta-oauth",
        line_user_id="U-owner-meta",
        role="owner",
    )

    _store_pending_state_memory(
        "state-meta-123",
        {"tenant_id": "tenant-meta-oauth", "platforms": ["meta"]},
        600,
    )

    short_token_response = MagicMock()
    short_token_response.status_code = 200
    short_token_response.json.return_value = {"access_token": "short-token"}

    long_token_response = MagicMock()
    long_token_response.status_code = 200
    long_token_response.json.return_value = {"access_token": "long-token"}

    debug_token_response = MagicMock()
    debug_token_response.status_code = 200
    debug_token_response.json.return_value = {"data": {"granular_scopes": []}}

    pages_response = MagicMock()
    pages_response.status_code = 200
    pages_response.json.return_value = {
        "data": [
            {
                "id": "fb-page-001",
                "name": "鴻笙堂 陳老師",
                "instagram_business_account": {"id": "ig-user-001"},
            }
        ]
    }

    page_token_response = MagicMock()
    page_token_response.status_code = 200
    page_token_response.json.return_value = {"access_token": "page-token-001"}

    with patch(
        "httpx.AsyncClient.get",
        side_effect=[short_token_response, long_token_response, debug_token_response, pages_response, page_token_response],
    ), patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        response = client.get(
            "/auth/meta/callback",
            params={"code": "meta-code", "state": "state-meta-123"},
        )

    assert response.status_code == 200
    assert "Meta 已連結成功" in response.text
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["to"] == "U-owner-meta"
    pushed_messages = push_mock.await_args.kwargs["messages"]
    assert any("目前渠道狀態" in message["text"] for message in pushed_messages)

    repo = app.state.repository
    connector = repo.get_connector_account("tenant-meta-oauth", "meta")
    assert connector is not None
    credentials = json.loads(connector.credentials_encrypted)
    assert credentials["access_token"] == "long-token"
    assert credentials["fb_access_token"] == "page-token-001"
    assert credentials["fb_page_id"] == "fb-page-001"
    assert credentials["ig_user_id"] == "ig-user-001"


def test_meta_callback_renders_page_selection_when_multiple_pages() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        KACHU_BASE_URL="http://localhost:8001",
        META_APP_ID="meta-app-id",
        META_APP_SECRET="meta-app-secret",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)

    _store_pending_state_memory(
        "state-meta-many",
        {"tenant_id": "tenant-meta-many", "platforms": ["meta"]},
        600,
    )

    short_token_response = MagicMock()
    short_token_response.status_code = 200
    short_token_response.json.return_value = {"access_token": "short-token"}

    long_token_response = MagicMock()
    long_token_response.status_code = 200
    long_token_response.json.return_value = {"access_token": "long-token"}

    debug_token_response = MagicMock()
    debug_token_response.status_code = 200
    debug_token_response.json.return_value = {
        "data": {
            "granular_scopes": [
                {"scope": "pages_show_list", "target_ids": ["fb-page-001", "fb-page-002", "fb-page-003"]}
            ]
        }
    }

    page_lookup_response = MagicMock()
    page_lookup_response.status_code = 200
    page_lookup_response.json.return_value = {
        "fb-page-001": {
            "id": "fb-page-001",
            "name": "粉專 A",
            "instagram_business_account": {"id": "ig-user-001"},
        },
        "fb-page-002": {
            "id": "fb-page-002",
            "name": "粉專 B",
        },
        "fb-page-003": {
            "id": "fb-page-003",
            "name": "粉專 C",
        },
    }

    with patch(
        "httpx.AsyncClient.get",
        side_effect=[short_token_response, long_token_response, debug_token_response, page_lookup_response],
    ), patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        response = client.get(
            "/auth/meta/callback",
            params={"code": "meta-code", "state": "state-meta-many"},
        )

    assert response.status_code == 200
    assert "選擇要綁定的 Facebook 粉專" in response.text
    assert "粉專 A" in response.text
    assert "粉專 B" in response.text
    assert "粉專 C" in response.text
    push_mock.assert_not_awaited()

    repo = app.state.repository
    connector = repo.get_connector_account("tenant-meta-many", "meta")
    assert connector is None


def test_meta_callback_falls_back_to_paginated_me_accounts_when_granular_scopes_missing() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        KACHU_BASE_URL="http://localhost:8001",
        META_APP_ID="meta-app-id",
        META_APP_SECRET="meta-app-secret",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)

    _store_pending_state_memory(
        "state-meta-fallback",
        {"tenant_id": "tenant-meta-fallback", "platforms": ["meta"]},
        600,
    )

    short_token_response = MagicMock()
    short_token_response.status_code = 200
    short_token_response.json.return_value = {"access_token": "short-token"}

    long_token_response = MagicMock()
    long_token_response.status_code = 200
    long_token_response.json.return_value = {"access_token": "long-token"}

    debug_token_response = MagicMock()
    debug_token_response.status_code = 200
    debug_token_response.json.return_value = {"data": {"granular_scopes": []}}

    pages_response_first = MagicMock()
    pages_response_first.status_code = 200
    pages_response_first.json.return_value = {
        "data": [
            {"id": "fb-page-001", "name": "粉專 A"},
            {"id": "fb-page-002", "name": "粉專 B"},
        ],
        "paging": {"cursors": {"after": "cursor-page-2"}},
    }

    pages_response_second = MagicMock()
    pages_response_second.status_code = 200
    pages_response_second.json.return_value = {
        "data": [
            {"id": "fb-page-003", "name": "粉專 C"},
        ]
    }

    with patch(
        "httpx.AsyncClient.get",
        side_effect=[
            short_token_response,
            long_token_response,
            debug_token_response,
            pages_response_first,
            pages_response_second,
        ],
    ), patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        response = client.get(
            "/auth/meta/callback",
            params={"code": "meta-code", "state": "state-meta-fallback"},
        )

    assert response.status_code == 200
    assert "粉專 A" in response.text
    assert "粉專 B" in response.text
    assert "粉專 C" in response.text
    push_mock.assert_not_awaited()


def test_meta_select_page_persists_chosen_page() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        KACHU_BASE_URL="http://localhost:8001",
        META_APP_ID="meta-app-id",
        META_APP_SECRET="meta-app-secret",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    client = TestClient(app)
    app.state.repository.create_tenant_membership(
        tenant_id="tenant-meta-many",
        line_user_id="U-owner-meta-many",
        role="owner",
    )

    _store_pending_state_memory(
        "selection-token-123",
        {
            "tenant_id": "tenant-meta-many",
            "meta_long_token": "long-token",
            "meta_pages": [
                {"id": "fb-page-001", "name": "粉專 A", "ig_user_id": "ig-user-001"},
                {"id": "fb-page-002", "name": "粉專 B", "ig_user_id": ""},
            ],
        },
        600,
    )

    with patch("kachu.auth.oauth.push_line_messages", new_callable=AsyncMock) as push_mock:
        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as get_mock:
            page_token_response = MagicMock()
            page_token_response.status_code = 200
            page_token_response.json.return_value = {"access_token": "page-token-002"}
            get_mock.return_value = page_token_response
            response = client.get(
                "/auth/meta/select-page",
                params={"selection_token": "selection-token-123", "page_id": "fb-page-002"},
            )

    assert response.status_code == 200
    assert "粉專 B" in response.text
    push_mock.assert_awaited_once()
    assert push_mock.await_args.kwargs["to"] == "U-owner-meta-many"
    pushed_messages = push_mock.await_args.kwargs["messages"]
    assert any("目前渠道狀態" in message["text"] for message in pushed_messages)

    repo = app.state.repository
    connector = repo.get_connector_account("tenant-meta-many", "meta")
    assert connector is not None
    credentials = json.loads(connector.credentials_encrypted)
    assert credentials["access_token"] == "long-token"
    assert credentials["fb_access_token"] == "page-token-002"
    assert credentials["fb_page_id"] == "fb-page-002"
    assert credentials["fb_page_name"] == "粉專 B"
    assert credentials["ig_user_id"] == ""


@pytest.mark.asyncio
async def test_oauth_push_line_texts_continues_after_single_recipient_failure() -> None:
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        LINE_CHANNEL_ACCESS_TOKEN="line-token",
        LINE_CHANNEL_SECRET="",
        AGENTOS_BASE_URL="http://agentos-mock",
    )
    app = create_app(settings)
    repo = app.state.repository
    repo.create_tenant_membership(
        tenant_id="tenant-multi-owner",
        line_user_id="U-owner-1",
        role="owner",
    )
    repo.create_tenant_membership(
        tenant_id="tenant-multi-owner",
        line_user_id="U-manager-1",
        role="manager",
    )

    failed_request = httpx.Request("POST", "https://api.line.me/v2/bot/message/push")

    with patch(
        "kachu.auth.oauth.push_line_messages",
        new=AsyncMock(side_effect=[httpx.ConnectError("boom", request=failed_request), None]),
    ) as push_mock:
        await _push_line_texts(settings, repo, "tenant-multi-owner", ["OAuth 完成"])

    assert push_mock.await_count == 2
    assert push_mock.await_args_list[0].kwargs["to"] == "U-owner-1"
    assert push_mock.await_args_list[1].kwargs["to"] == "U-manager-1"