from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.config import Settings
from kachu_plus.google_business import GoogleReviewService, router
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import TenantTable


class _FakeGoogleBusinessClient:
    def __init__(self, *, access_token: str = "") -> None:
        self.access_token = access_token

    def list_reviews(self, account_id: str, location_id: str, page_size: int = 10):
        return [
            {
                "reviewId": "review-1",
                "starRating": "FIVE",
                "comment": "服務很好，會再回來。",
                "reviewer": {"displayName": "王小美"},
                "createTime": "2026-05-09T02:00:00Z",
            }
        ]

    def get_review(self, account_id: str, location_id: str, review_id: str):
        return {
            "reviewId": review_id,
            "starRating": "FOUR",
            "comment": "餐點不錯。",
            "reviewer": {"displayName": "陳先生"},
            "createTime": "2026-05-09T01:00:00Z",
        }


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _make_app(repo: KachuPlusRepository, settings: Settings, service: GoogleReviewService) -> FastAPI:
    app = FastAPI()
    app.state.repository = repo
    app.state.settings = settings
    app.state.google_review_service = service
    app.include_router(router)
    return app


def _seed_tenant(repo: KachuPlusRepository, tenant_id: str = "tenant-1") -> None:
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id=tenant_id, name="測試店"))
        session.commit()


def test_google_business_connector_and_reviews_endpoint() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    service = GoogleReviewService(repo, settings, client_factory=lambda **kwargs: _FakeGoogleBusinessClient(**kwargs))
    client = TestClient(_make_app(repo, settings, service))

    saved = client.put(
        "/tenants/tenant-1/google-business/connector",
        json={
            "account_label": "Google Business Profile",
            "account_id": "123",
            "location_id": "locations/456",
            "access_token": "token-abc",
            "expires_at": 9999999999,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["connector"]["account_id"] == "123"

    reviews = client.get("/tenants/tenant-1/google-business/reviews")
    assert reviews.status_code == 200
    assert reviews.json()["reviews"][0]["review_id"] == "review-1"
    assert reviews.json()["reviews"][0]["reviewer_name"] == "王小美"

    fetched = client.post("/tools/fetch-review", json={"tenant_id": "tenant-1", "review_id": "review-99"})
    assert fetched.status_code == 200
    assert fetched.json()["review_id"] == "review-99"
    assert fetched.json()["reviewer_name"] == "陳先生"


def test_google_business_reviews_graceful_error_when_expired_without_refresh_token() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    service = GoogleReviewService(repo, settings, client_factory=lambda **kwargs: _FakeGoogleBusinessClient(**kwargs))
    service.upsert_connector(
        tenant_id="tenant-1",
        account_label="GBP",
        account_id="123",
        location_id="locations/456",
        access_token="expired-token",
        refresh_token=None,
        expires_at=1,
    )
    client = TestClient(_make_app(repo, settings, service))

    reviews = client.get("/tenants/tenant-1/google-business/reviews")
    assert reviews.status_code == 409
    assert "expired" in reviews.json()["detail"]