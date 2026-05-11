from __future__ import annotations

import json

from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.config import Settings
from kachu_plus.meta import MetaInsightsService, MetaOAuthFlowService, router
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import TenantTable


class _FakeMetaClient:
    def __init__(self, *, access_token: str = "", fb_page_id: str = "", ig_user_id: str = "", fb_access_token: str = "") -> None:
        self.access_token = access_token
        self.fb_page_id = fb_page_id
        self.ig_user_id = ig_user_id
        self.fb_access_token = fb_access_token

    def get_fb_page_insights(self, *, period: str = "week", metric_names=None):
        return {
            "data": [
                {"name": "page_impressions_unique", "values": [{"value": 1800}]},
                {"name": "page_post_engagements", "values": [{"value": 240}]},
            ]
        }

    def get_fb_post_insights(self, *, post_id: str, metric_names=None):
        return {
            "data": [
                {"name": "post_impressions", "values": [{"value": 600}]},
                {"name": "post_engagements", "values": [{"value": 72}]},
            ]
        }

    def get_ig_media_insights(self, *, media_id: str, metric_names=None):
        return {
            "data": [
                {"name": "impressions", "values": [{"value": 420}]},
                {"name": "reach", "values": [{"value": 260}]},
            ]
        }


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_tenant(repo: KachuPlusRepository, tenant_id: str = "tenant-1") -> None:
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id=tenant_id, name="測試店"))
        session.commit()


def _make_app(repo: KachuPlusRepository, settings: Settings, service: MetaInsightsService) -> FastAPI:
    app = FastAPI()
    app.state.repository = repo
    app.state.settings = settings
    app.state.meta_insights_service = service
    consultant = MagicMock()
    consultant.build_reply = AsyncMock(return_value="這週 Facebook 觸及穩定，建議把高互動貼文加上更明確的預約 CTA。")
    app.state.consultant = consultant
    app.include_router(router)
    return app


def test_meta_connector_and_insights_endpoints() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "push-token"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    saved = client.put(
        "/tenants/tenant-1/meta/connector",
        json={
            "account_label": "Meta 測試粉專",
            "access_token": "meta-token",
            "fb_page_id": "fb-page-1",
            "ig_user_id": "ig-user-1",
            "recent_fb_post_id": "fb-post-1",
            "recent_ig_media_id": "ig-media-1",
            "expires_at": 9999999999,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["connector"]["fb_page_id"] == "fb-page-1"

    insights = client.get("/tenants/tenant-1/meta/insights", params={"period": "week"})
    assert insights.status_code == 200
    payload = insights.json()
    assert payload["facebook_page_insights"]["page_impressions_unique"] == 1800
    assert payload["facebook_post_insights"]["post_engagements"] == 72
    assert payload["instagram_media_insights"]["reach"] == 260

    tool_fetch = client.post(
        "/tools/fetch-meta-insights",
        json={"tenant_id": "tenant-1", "period": "week"},
    )
    assert tool_fetch.status_code == 200
    assert tool_fetch.json()["facebook_page_insights"]["page_post_engagements"] == 240

    summary = client.post(
        "/tools/generate-meta-insights-summary",
        json={"tenant_id": "tenant-1", "insights": payload},
    )
    assert summary.status_code == 200
    assert "Facebook" in summary.json()["summary"]
    assert any(item["label"] == "觸及人數" for item in summary.json()["details"])

    with patch("kachu_plus.meta.push_line_messages", new=AsyncMock()) as mock_push:
        report = client.post(
            "/tools/send-meta-insights-report",
            json={
                "tenant_id": "tenant-1",
                "summary": summary.json()["summary"],
                "details": summary.json()["details"],
                "period": "week",
                "recipient_line_ids": ["U-owner-1"],
            },
        )

    assert report.status_code == 200
    assert report.json()["status"] == "sent"
    mock_push.assert_awaited_once()
    sent_message = mock_push.await_args.kwargs["messages"][0]
    assert sent_message["type"] == "flex"
    assert sent_message["altText"].startswith("Meta 成效報告")


def test_meta_insights_graceful_error_when_expired() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    service.upsert_connector(
        tenant_id="tenant-1",
        account_label="Meta 測試粉專",
        access_token="expired-token",
        fb_page_id="fb-page-1",
        expires_at=1,
    )
    client = TestClient(_make_app(repo, settings, service))

    insights = client.get("/tenants/tenant-1/meta/insights")
    assert insights.status_code == 409
    assert "expired" in insights.json()["detail"]


def test_meta_oauth_start_creates_session() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.META_APP_ID = "meta-app-id"
    settings.META_APP_SECRET = "meta-app-secret"
    settings.META_OAUTH_REDIRECT_URI = "https://plus.kachu.tw/meta/callback"
    settings.META_OAUTH_SCOPES = "pages_show_list,pages_read_engagement,instagram_basic"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    response = client.post(
        "/tenants/tenant-1/meta/connect/start",
        json={"line_user_id": "U-owner-1"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["authorize_url"].startswith("https://www.facebook.com/")
    session = repo.get_meta_oauth_session(payload["session_id"])
    assert session is not None
    assert session.line_user_id == "U-owner-1"
    assert session.status == "pending"


def test_meta_oauth_select_page_requires_overwrite_confirmation() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    service.upsert_connector(
        tenant_id="tenant-1",
        account_label="既有粉專",
        access_token="existing-token",
        fb_page_id="fb-page-existing",
        ig_user_id="ig-existing",
    )
    oauth_session = repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="U-owner-1",
        state="state-1",
    )
    repo.update_meta_oauth_session(
        session_id=oauth_session.id,
        status="selecting_page",
        user_access_token="user-token",
        page_candidates=[
            {
                "page_id": "fb-page-new",
                "page_name": "新粉專",
                "page_access_token": "page-token-1",
                "ig_user_id": "",
                "ig_username": "",
            }
        ],
    )

    response = client.post(
        f"/meta/connect/{oauth_session.id}/select-page",
        json={"page_id": "fb-page-new", "overwrite_existing": False},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "overwrite_required"
    updated_session = repo.get_meta_oauth_session(oauth_session.id)
    assert updated_session is not None
    assert updated_session.status == "awaiting_overwrite_confirmation"
    assert updated_session.selected_page_id == "fb-page-new"


def test_meta_oauth_callback_falls_back_to_granular_scope_page_targets() -> None:
    class _FakeResponse:
        def __init__(self, payload: dict[str, object]) -> None:
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return self._payload

    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.META_APP_ID = "meta-app-id"
    settings.META_APP_SECRET = "meta-app-secret"
    service = MetaOAuthFlowService(repo, settings)
    oauth_session = repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="U-owner-1",
        state="state-1",
    )

    with patch.object(service, "_exchange_code_for_user_token", return_value="user-token"):
        with patch(
            "kachu_plus.meta.httpx.get",
            side_effect=[
                _FakeResponse({"data": []}),
                _FakeResponse(
                    {
                        "data": {
                            "granular_scopes": [
                                {"scope": "pages_show_list", "target_ids": ["940149472511909"]},
                                {"scope": "pages_read_engagement", "target_ids": ["940149472511909"]},
                            ]
                        }
                    }
                ),
                _FakeResponse(
                    {
                        "id": "940149472511909",
                        "name": "四時循養堂（原坐骨新經）",
                        "access_token": "page-token-1",
                    }
                ),
            ],
        ):
            payload = service.handle_callback(state="state-1", code="oauth-code")

    assert payload["status"] == "selecting_page"
    assert payload["pages"] == [
        {
            "page_id": "940149472511909",
            "page_name": "四時循養堂（原坐骨新經）",
            "page_access_token": "page-token-1",
            "ig_user_id": "",
            "ig_username": "",
        }
    ]
    updated_session = repo.get_meta_oauth_session(oauth_session.id)
    assert updated_session is not None
    assert updated_session.status == "selecting_page"


def test_meta_disconnect_deactivates_connector() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    service.upsert_connector(
        tenant_id="tenant-1",
        account_label="Meta 測試粉專",
        access_token="meta-token",
        fb_page_id="fb-page-1",
    )

    response = client.post(
        "/tenants/tenant-1/meta/disconnect",
        json={"line_user_id": "U-owner-1"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "disconnected"
    assert repo.get_connector_account("tenant-1", "meta") is None


def test_meta_manage_page_renders_connection_state() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    service.upsert_connector(
        tenant_id="tenant-1",
        account_label="四時循養堂",
        access_token="meta-token",
        fb_page_id="fb-page-1",
        ig_user_id="ig-user-1",
    )
    client = TestClient(_make_app(repo, settings, service))

    response = client.get("/tenants/tenant-1/meta/manage")

    assert response.status_code == 200
    assert "Meta 連接管理" in response.text
    assert "四時循養堂" in response.text
    assert "Instagram：已連接" in response.text


def test_meta_manage_page_collapses_multiple_pending_sessions() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="U-owner-1",
        state="state-1",
    )
    repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="U-owner-1",
        state="state-2",
    )

    response = client.get("/tenants/tenant-1/meta/manage")

    assert response.status_code == 200
    assert "目前有 2 筆未完成流程" in response.text
    assert response.text.count("繼續授權流程") == 1


def test_meta_callback_redirects_to_session_page_after_fetching_pages() -> None:
    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.META_APP_ID = "meta-app-id"
    settings.META_APP_SECRET = "meta-app-secret"
    settings.META_OAUTH_REDIRECT_URI = "https://plus.kachu.tw/meta/oauth/callback"
    settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    start = client.post("/tenants/tenant-1/meta/connect/start", json={"line_user_id": "U-owner-1"})
    session = repo.get_meta_oauth_session(start.json()["session_id"])
    assert session is not None

    with patch(
        "kachu_plus.meta.httpx.get",
        side_effect=[
            _FakeResponse({"access_token": "user-token"}),
            _FakeResponse({"data": [{"id": "fb-page-1", "name": "四時循養堂", "access_token": "page-token-1"}]}),
        ],
    ):
        response = client.get(f"/meta/oauth/callback?state={session.state}&code=oauth-code", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].endswith(f"/meta/connect/{session.id}/page")


def test_meta_select_page_web_returns_success_html() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    oauth_session = repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="",
        state="state-select-web",
    )
    repo.update_meta_oauth_session(
        session_id=oauth_session.id,
        status="selecting_page",
        user_access_token="user-token",
        page_candidates=[
            {
                "page_id": "fb-page-1",
                "page_name": "四時循養堂",
                "page_access_token": "page-token-1",
                "ig_user_id": "",
                "ig_username": "",
            }
        ],
    )

    response = client.get(
        f"/meta/connect/{oauth_session.id}/select-page-web",
        params={"page_id": "fb-page-1", "overwrite_existing": "false"},
    )

    assert response.status_code == 200
    assert "已成功連接 Facebook 粉專" in response.text
    assert "四時循養堂" in response.text


def test_meta_connect_session_page_renders_simplified_page_selection() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    oauth_session = repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="",
        state="state-selection-page",
    )
    repo.update_meta_oauth_session(
        session_id=oauth_session.id,
        status="selecting_page",
        user_access_token="user-token",
        page_candidates=[
            {
                "page_id": "fb-page-1",
                "page_name": "四時循養堂",
                "page_access_token": "page-token-1",
                "ig_user_id": "ig-user-1",
                "ig_username": "seasonwell",
            },
            {
                "page_id": "fb-page-2",
                "page_name": "備用粉專",
                "page_access_token": "page-token-2",
                "ig_user_id": "",
                "ig_username": "",
            },
        ],
    )

    response = client.get(f"/meta/connect/{oauth_session.id}/page")

    assert response.status_code == 200
    assert "先選你要連接的 Facebook 粉專" in response.text
    assert response.text.count("連接這個粉專") == 2


def test_meta_connect_session_page_renders_simplified_error_page() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.KACHU_BASE_URL = "https://plus.kachu.tw"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    oauth_session = repo.create_meta_oauth_session(
        tenant_id="tenant-1",
        line_user_id="",
        state="state-error-page",
    )
    repo.update_meta_oauth_session(
        session_id=oauth_session.id,
        status="failed",
        error_message="Meta 授權流程未完成，請重新開始。",
    )

    response = client.get(f"/meta/connect/{oauth_session.id}/page")

    assert response.status_code == 409
    assert "這次沒有完成" in response.text
    assert "通常怎麼處理" in response.text


def test_meta_webhook_verification_returns_challenge() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    settings.META_WEBHOOK_VERIFY_TOKEN = "verify-token"
    service = MetaInsightsService(repo, settings, client_factory=lambda **kwargs: _FakeMetaClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    response = client.get(
        "/meta/webhook",
        params={"hub.mode": "subscribe", "hub.verify_token": "verify-token", "hub.challenge": "12345"},
    )

    assert response.status_code == 200
    assert response.text == "12345"