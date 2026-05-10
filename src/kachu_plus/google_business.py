from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any, Callable

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel

from kachu_plus.config import Settings

router = APIRouter(tags=["google-business", "tools"])


class GoogleBusinessConnectorError(ValueError):
    pass


class GoogleBusinessClient:
    _GBP_BASE = "https://mybusiness.googleapis.com/v4"

    def __init__(self, *, access_token: str = "") -> None:
        self._access_token = access_token.strip()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs):
        response = httpx.request(method, url, headers=self._headers(), timeout=15.0, **kwargs)
        response.raise_for_status()
        return response

    def _location_parent(self, account_id: str, location_id: str) -> str:
        normalized_account_id = account_id.strip().strip("/")
        normalized_location_id = location_id.strip().strip("/")
        if normalized_location_id.startswith("accounts/"):
            return normalized_location_id
        if normalized_account_id.startswith("accounts/"):
            return f"{normalized_account_id}/{normalized_location_id}"
        return f"accounts/{normalized_account_id}/{normalized_location_id}"

    def list_reviews(self, account_id: str, location_id: str, page_size: int = 10) -> list[dict[str, Any]]:
        url = f"{self._GBP_BASE}/{self._location_parent(account_id, location_id)}/reviews"
        response = self._request("GET", url, params={"pageSize": page_size})
        return response.json().get("reviews", [])

    def get_review(self, account_id: str, location_id: str, review_id: str) -> dict[str, Any]:
        url = f"{self._GBP_BASE}/{self._location_parent(account_id, location_id)}/reviews/{review_id}"
        response = self._request("GET", url)
        return response.json()

    def post_reply(self, account_id: str, location_id: str, review_id: str, reply_text: str) -> dict[str, Any]:
        url = f"{self._GBP_BASE}/{self._location_parent(account_id, location_id)}/reviews/{review_id}/reply"
        response = self._request(
            "PUT",
            url,
            content=json.dumps({"comment": reply_text}, ensure_ascii=False).encode(),
        )
        return response.json()

    def create_local_post(
        self,
        account_id: str,
        location_id: str,
        summary: str,
        call_to_action_url: str = "",
    ) -> dict[str, Any]:
        url = f"{self._GBP_BASE}/{self._location_parent(account_id, location_id)}/localPosts"
        body: dict[str, Any] = {
            "languageCode": "zh-TW",
            "summary": summary,
            "topicType": "STANDARD",
        }
        if call_to_action_url:
            body["callToAction"] = {"actionType": "LEARN_MORE", "url": call_to_action_url}
        response = self._request("POST", url, content=json.dumps(body, ensure_ascii=False).encode())
        return response.json()


def load_connector_credentials(account: Any) -> dict[str, Any]:
    raw = getattr(account, "credentials_json", "") or "{}"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_google_business_client_context(
    *,
    repo: Any,
    settings: Settings,
    tenant_id: str,
    client_factory: Callable[..., GoogleBusinessClient] | None = None,
) -> tuple[GoogleBusinessClient, str, str]:
    service = GoogleReviewService(repo, settings, client_factory=client_factory)
    return service._resolve_client_context(tenant_id)  # noqa: SLF001


class GoogleReviewService:
    def __init__(
        self,
        repo: Any,
        settings: Settings,
        client_factory: Callable[..., GoogleBusinessClient] | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._client_factory = client_factory or (lambda **kwargs: GoogleBusinessClient(**kwargs))

    def upsert_connector(
        self,
        *,
        tenant_id: str,
        account_label: str,
        account_id: str,
        location_id: str,
        access_token: str,
        refresh_token: str | None = None,
        expires_at: int | None = None,
    ) -> Any:
        credentials = {
            "account_id": account_id,
            "location_id": location_id,
            "access_token": access_token,
            "refresh_token": refresh_token or "",
            "expires_at": int(expires_at or 0),
            "refresh_status": "healthy",
        }
        return self._repo.save_connector_account(
            tenant_id=tenant_id,
            platform="google_business",
            account_label=account_label,
            credentials_json=json.dumps(credentials, ensure_ascii=False),
            touch_refreshed_at=True,
        )

    def get_connector_status(self, tenant_id: str) -> dict[str, Any] | None:
        account = self._repo.get_connector_account(tenant_id, "google_business")
        if account is None:
            return None
        credentials = load_connector_credentials(account)
        return {
            "platform": account.platform,
            "account_label": account.account_label,
            "account_id": credentials.get("account_id", ""),
            "location_id": credentials.get("location_id", ""),
            "expires_at": credentials.get("expires_at"),
            "refresh_status": credentials.get("refresh_status", "healthy"),
            "has_refresh_token": bool(credentials.get("refresh_token")),
            "last_refreshed_at": account.last_refreshed_at.isoformat() if account.last_refreshed_at else None,
        }

    def list_reviews(self, tenant_id: str, *, page_size: int = 10) -> list[dict[str, Any]]:
        client, account_id, location_id = self._resolve_client_context(tenant_id)
        return client.list_reviews(account_id, location_id, page_size=page_size)

    def fetch_review(self, tenant_id: str, *, review_id: str) -> dict[str, Any]:
        client, account_id, location_id = self._resolve_client_context(tenant_id)
        return client.get_review(account_id, location_id, review_id)

    def resolve_client_context(self, tenant_id: str) -> tuple[GoogleBusinessClient, str, str]:
        return self._resolve_client_context(tenant_id)

    def _resolve_client_context(self, tenant_id: str) -> tuple[GoogleBusinessClient, str, str]:
        account = self._repo.get_connector_account(tenant_id, "google_business")
        if account is not None:
            credentials = self._refresh_credentials_if_needed(tenant_id, load_connector_credentials(account))
            access_token = str(credentials.get("access_token", "") or "").strip()
            account_id = str(credentials.get("account_id", "") or "").strip()
            location_id = str(credentials.get("location_id", "") or "").strip()
            if access_token and account_id and location_id:
                return self._client_factory(access_token=access_token), account_id, location_id
            raise GoogleBusinessConnectorError("google_business connector is incomplete")

        if (
            self._settings.GOOGLE_SERVICE_ACCOUNT_JSON
            and self._settings.GOOGLE_BUSINESS_ACCOUNT_ID
            and self._settings.GOOGLE_BUSINESS_LOCATION_ID
        ):
            raise GoogleBusinessConnectorError("service-account fallback is not implemented in Kachu+ yet")

        raise GoogleBusinessConnectorError("google_business connector is not configured")

    def _refresh_credentials_if_needed(self, tenant_id: str, credentials: dict[str, Any]) -> dict[str, Any]:
        expires_at = int(credentials.get("expires_at", 0) or 0)
        if not expires_at or time.time() <= expires_at - 300:
            return credentials

        refresh_token = str(credentials.get("refresh_token", "") or "").strip()
        if not refresh_token:
            raise GoogleBusinessConnectorError("google_business credential expired and no refresh token is available")
        if not self._settings.GOOGLE_OAUTH_CLIENT_ID or not self._settings.GOOGLE_OAUTH_CLIENT_SECRET:
            raise GoogleBusinessConnectorError("google_business credential expired and OAuth client is not configured")

        response = httpx.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": self._settings.GOOGLE_OAUTH_CLIENT_ID,
                "client_secret": self._settings.GOOGLE_OAUTH_CLIENT_SECRET,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
            timeout=10.0,
        )
        if response.status_code != 200:
            credentials["refresh_status"] = "failed"
            credentials["last_refresh_error"] = response.text[:200]
            self._repo.update_connector_account(
                tenant_id=tenant_id,
                platform="google_business",
                credentials_json=json.dumps(credentials, ensure_ascii=False),
                touch_refreshed_at=False,
            )
            raise GoogleBusinessConnectorError("google_business credential refresh failed")

        payload = response.json()
        credentials["access_token"] = payload["access_token"]
        credentials["expires_at"] = int(time.time()) + int(payload.get("expires_in", 3600))
        credentials["refresh_status"] = "healthy"
        self._repo.update_connector_account(
            tenant_id=tenant_id,
            platform="google_business",
            credentials_json=json.dumps(credentials, ensure_ascii=False),
            touch_refreshed_at=True,
        )
        return credentials


class GoogleBusinessConnectorUpsertRequest(BaseModel):
    account_label: str = ""
    account_id: str
    location_id: str
    access_token: str
    refresh_token: str | None = None
    expires_at: int | None = None


class FetchReviewRequest(BaseModel):
    tenant_id: str
    review_id: str


def _get_service(request: Request) -> GoogleReviewService:
    service = getattr(request.app.state, "google_review_service", None)
    if service is None:
        service = GoogleReviewService(request.app.state.repository, request.app.state.settings)
        request.app.state.google_review_service = service
    return service


def _normalize_review(review: dict[str, Any]) -> dict[str, Any]:
    return {
        "review_id": review.get("reviewId") or review.get("name", "").split("/")[-1],
        "rating": review.get("starRating", ""),
        "content": review.get("comment", ""),
        "reviewer_name": review.get("reviewer", {}).get("displayName", "顧客"),
        "created_at": review.get("createTime", ""),
    }


@router.put("/tenants/{tenant_id}/google-business/connector", status_code=status.HTTP_200_OK)
def upsert_google_business_connector(
    tenant_id: str,
    payload: GoogleBusinessConnectorUpsertRequest,
    request: Request,
) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="tenant not found")

    service = _get_service(request)
    service.upsert_connector(
        tenant_id=tenant_id,
        account_label=payload.account_label,
        account_id=payload.account_id,
        location_id=payload.location_id,
        access_token=payload.access_token,
        refresh_token=payload.refresh_token,
        expires_at=payload.expires_at,
    )
    status_view = service.get_connector_status(tenant_id)
    return {"status": "ok", "connector": status_view}


@router.get("/tenants/{tenant_id}/google-business/connector")
def get_google_business_connector(tenant_id: str, request: Request) -> dict[str, Any]:
    service = _get_service(request)
    status_view = service.get_connector_status(tenant_id)
    if status_view is None:
        raise HTTPException(status_code=404, detail="google_business connector not found")
    return status_view


@router.get("/tenants/{tenant_id}/google-business/reviews")
def list_google_business_reviews(
    tenant_id: str,
    request: Request,
    page_size: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    service = _get_service(request)
    try:
        reviews = service.list_reviews(tenant_id, page_size=page_size)
    except GoogleBusinessConnectorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"reviews": [_normalize_review(review) for review in reviews]}


@router.post("/tools/fetch-review")
def fetch_review_tool(payload: FetchReviewRequest, request: Request) -> dict[str, Any]:
    service = _get_service(request)
    try:
        review = service.fetch_review(payload.tenant_id, review_id=payload.review_id)
    except GoogleBusinessConnectorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _normalize_review(review)