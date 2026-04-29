from __future__ import annotations

import json
from base64 import b64encode
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import patch

import httpx
import pytest
from fastapi.testclient import TestClient

from kachu.auth import oauth as oauth_module
from kachu.config import Settings
from kachu.main import create_app


def test_create_app_rejects_missing_production_config() -> None:
    with pytest.raises(RuntimeError, match="Missing required production config"):
        create_app(
            Settings(
                APP_ENV="production",
                DATABASE_URL="sqlite://",
                SECRET_KEY="",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_CHANNEL_SECRET="secret",
                TOKEN_ENCRYPTION_KEY="encrypt",
                GOOGLE_AI_API_KEY="",
                OPENAI_API_KEY="",
                ALLOW_SCHEMA_CREATE_IN_PRODUCTION=True,
            )
        )


def test_create_app_rejects_production_schema_autocreate_without_opt_in() -> None:
    with pytest.raises(RuntimeError, match="Automatic schema creation is disabled in production"):
        create_app(
            Settings(
                APP_ENV="production",
                DATABASE_URL="sqlite://",
                SECRET_KEY="secret",
                TOKEN_ENCRYPTION_KEY="encrypt",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_CHANNEL_SECRET="secret",
                OPENAI_API_KEY="openai-key",
            )
        )


def test_create_app_requires_meta_secret_when_feature_enabled() -> None:
    with pytest.raises(RuntimeError, match="META_APP_SECRET"):
        create_app(
            Settings(
                APP_ENV="production",
                DATABASE_URL="sqlite://",
                SECRET_KEY="secret",
                TOKEN_ENCRYPTION_KEY="encrypt",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_CHANNEL_SECRET="line-secret",
                OPENAI_API_KEY="openai-key",
                FEATURE_META=True,
                META_APP_ID="meta-app-id",
                META_APP_SECRET="",
                ALLOW_SCHEMA_CREATE_IN_PRODUCTION=True,
            )
        )


def test_create_app_rejects_memory_oauth_state_store_in_production() -> None:
    with pytest.raises(RuntimeError, match="OAUTH_STATE_STORE_BACKEND"):
        create_app(
            Settings(
                APP_ENV="production",
                DATABASE_URL="sqlite://",
                SECRET_KEY="secret",
                TOKEN_ENCRYPTION_KEY="encrypt",
                LINE_CHANNEL_ACCESS_TOKEN="token",
                LINE_CHANNEL_SECRET="line-secret",
                OPENAI_API_KEY="openai-key",
                ALLOW_SCHEMA_CREATE_IN_PRODUCTION=True,
                OAUTH_STATE_STORE_BACKEND="memory",
            )
        )


def test_line_webhook_returns_503_when_secret_missing_outside_test() -> None:
    client = TestClient(
        create_app(
            Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                LINE_CHANNEL_ACCESS_TOKEN="",
                LINE_CHANNEL_SECRET="",
            )
        )
    )

    response = client.post("/webhooks/line", json={"events": []})
    assert response.status_code == 503
    assert response.json()["detail"] == "LINE webhook misconfigured"


def test_google_review_webhook_requires_bearer_secret_when_configured() -> None:
    client = TestClient(
        create_app(
            Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                GOOGLE_WEBHOOK_SHARED_SECRET="shared-secret",
            )
        )
    )

    response = client.post("/webhooks/google/review", json={"message": {"data": ""}})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authorization"


def test_google_review_webhook_requires_auth_even_without_secret_in_development() -> None:
    client = TestClient(
        create_app(
            Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
            )
        )
    )

    response = client.post("/webhooks/google/review", json={"message": {"data": ""}})
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid webhook authorization"


def test_google_review_webhook_accepts_verified_oidc_token() -> None:
    app = create_app(
        Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            GOOGLE_WEBHOOK_OIDC_AUDIENCE="https://example.com/webhooks/google/review",
        )
    )
    client = TestClient(app)

    with patch("kachu.google.webhook.google_id_token.verify_oauth2_token") as verify_mock:
        verify_mock.return_value = {
            "iss": "https://accounts.google.com",
            "aud": "https://example.com/webhooks/google/review",
        }
        response = client.post(
            "/webhooks/google/review",
            headers={"Authorization": "Bearer oidc-token"},
            json={"message": {"data": "", "messageId": "msg-1"}},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["note"] == "ping"


def test_google_review_webhook_ignores_unmapped_location() -> None:
    client = TestClient(
        create_app(
            Settings(
                APP_ENV="development",
                DATABASE_URL="sqlite://",
                GOOGLE_WEBHOOK_SHARED_SECRET="shared-secret",
            )
        )
    )

    payload = {"reviewId": "review-1", "locationName": "accounts/1/locations/loc-1"}
    response = client.post(
        "/webhooks/google/review",
        headers={"Authorization": "Bearer shared-secret"},
        json={"message": {"data": b64encode(json.dumps(payload).encode()).decode(), "messageId": "msg-1"}},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "ignored"
    assert response.json()["reason"] == "no_tenant_mapping"


def test_google_review_webhook_routes_to_tenant_by_connector_location() -> None:
    app = create_app(
        Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            GOOGLE_WEBHOOK_SHARED_SECRET="shared-secret",
        )
    )
    repo = app.state.repository
    repo.get_or_create_tenant("tenant-1")
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        credentials_json=json.dumps({"location_id": "loc-1"}),
        account_label="Google Business Profile",
    )

    agentos = AsyncMock()
    agentos.create_task.return_value = SimpleNamespace(task={"id": "task-1"})
    agentos.run_task.return_value = SimpleNamespace(run={"id": "run-1"})
    app.state.agentOS_client = agentos
    client = TestClient(app)

    payload = {"reviewId": "review-1", "locationName": "accounts/1/locations/loc-1"}
    response = client.post(
        "/webhooks/google/review",
        headers={"Authorization": "Bearer shared-secret"},
        json={"message": {"data": b64encode(json.dumps(payload).encode()).decode(), "messageId": "msg-1"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["triggered"] == "1"
    task_request = agentos.create_task.call_args[0][0]
    assert task_request.tenant_id == "tenant-1"
    assert task_request.workflow_input["location_name"] == "accounts/1/locations/loc-1"


def test_google_review_webhook_logs_recoverable_agentos_error() -> None:
    app = create_app(
        Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            GOOGLE_WEBHOOK_SHARED_SECRET="shared-secret",
        )
    )
    repo = app.state.repository
    repo.get_or_create_tenant("tenant-1")
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        credentials_json=json.dumps({"location_id": "loc-1"}),
        account_label="Google Business Profile",
    )

    agentos = AsyncMock()
    agentos.create_task.side_effect = httpx.ReadTimeout("timeout")
    app.state.agentOS_client = agentos
    client = TestClient(app)

    payload = {"reviewId": "review-1", "locationName": "accounts/1/locations/loc-1"}
    response = client.post(
        "/webhooks/google/review",
        headers={"Authorization": "Bearer shared-secret"},
        json={"message": {"data": b64encode(json.dumps(payload).encode()).decode(), "messageId": "msg-1"}},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["triggered"] == "0"


def test_google_review_webhook_re_raises_unexpected_agentos_error() -> None:
    app = create_app(
        Settings(
            APP_ENV="development",
            DATABASE_URL="sqlite://",
            GOOGLE_WEBHOOK_SHARED_SECRET="shared-secret",
        )
    )
    repo = app.state.repository
    repo.get_or_create_tenant("tenant-1")
    repo.save_connector_account(
        tenant_id="tenant-1",
        platform="google_business",
        credentials_json=json.dumps({"location_id": "loc-1"}),
        account_label="Google Business Profile",
    )

    agentos = AsyncMock()
    agentos.create_task.side_effect = AssertionError("unexpected")
    app.state.agentOS_client = agentos
    client = TestClient(app)

    payload = {"reviewId": "review-1", "locationName": "accounts/1/locations/loc-1"}
    with pytest.raises(AssertionError, match="unexpected"):
        client.post(
            "/webhooks/google/review",
            headers={"Authorization": "Bearer shared-secret"},
            json={"message": {"data": b64encode(json.dumps(payload).encode()).decode(), "messageId": "msg-1"}},
        )


@pytest.mark.asyncio
async def test_oauth_pending_state_expires_after_ttl() -> None:
    oauth_module._pending_states.clear()
    settings = Settings(
        APP_ENV="development",
        DATABASE_URL="sqlite://",
        OAUTH_STATE_STORE_BACKEND="memory",
        OAUTH_STATE_TTL_SECONDS=600,
    )

    with patch("kachu.auth.oauth.time.time", return_value=1_000.0):
        await oauth_module._store_pending_state(
            settings,
            "state-1",
            {"tenant_id": "tenant-1", "platforms": ["gbp"]},
        )

    with patch(
        "kachu.auth.oauth.time.time",
        return_value=1_000.0 + oauth_module._STATE_TTL_SECONDS + 1,
    ):
        assert await oauth_module._pop_pending_state(settings, "state-1") is None

    assert "state-1" not in oauth_module._pending_states