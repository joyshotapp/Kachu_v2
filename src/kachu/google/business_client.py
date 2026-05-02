from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

# Google Business Profile API v4
_GBP_BASE = "https://mybusiness.googleapis.com/v4"
_MY_BUSINESS_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"
_BUSINESS_INFO_BASE = "https://mybusinessbusinessinformation.googleapis.com/v1"

# Scopes required
SCOPES = [
    "https://www.googleapis.com/auth/business.manage",
]


def _build_credentials(service_account_json_path: str):
    """Load Google service account credentials from file path."""
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(
        service_account_json_path,
        scopes=SCOPES,
    )


def _get_token(credentials) -> str:
    """Refresh and return bearer token."""
    import google.auth.transport.requests

    request = google.auth.transport.requests.Request()
    credentials.refresh(request)
    return credentials.token


def _retry_delay_seconds(response) -> int:
    raw_value = response.headers.get("Retry-After", "")
    try:
        delay = int(raw_value)
    except (TypeError, ValueError):
        delay = 65
    return max(delay, 1)


class GoogleBusinessClient:
    """
    Client for Google Business Profile API.
    Handles review fetching and reply posting.
    """

    def __init__(
        self,
        service_account_json_path: str | None = None,
        *,
        access_token: str = "",
    ) -> None:
        self._creds = _build_credentials(service_account_json_path) if service_account_json_path else None
        self._access_token = access_token.strip()

    @classmethod
    def from_oauth_token(cls, access_token: str) -> "GoogleBusinessClient":
        """Create a client that uses an OAuth bearer token instead of a service account."""
        return cls(access_token=access_token)

    def _headers(self) -> dict[str, str]:
        token = self._access_token or _get_token(self._creds)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, url: str, **kwargs):
        import httpx

        response = httpx.request(method, url, headers=self._headers(), timeout=15.0, **kwargs)
        if response.status_code == 429:
            delay = _retry_delay_seconds(response)
            logger.warning("Google Business API rate-limited for %s %s; retrying in %ss", method, url, delay)
            time.sleep(delay)
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

    # ── Account and location discovery ──────────────────────────────────────

    def list_accounts(self) -> list[dict[str, Any]]:
        """List Google Business accounts available to the current credentials."""
        resp = self._request("GET", f"{_MY_BUSINESS_BASE}/accounts")
        return resp.json().get("accounts", [])

    def list_locations(self, account_id: str, page_size: int = 100) -> list[dict[str, Any]]:
        """List locations for an account and normalize names to full account/location resources."""
        normalized_account_id = account_id.strip().rstrip("/")
        resp = self._request(
            "GET",
            f"{_BUSINESS_INFO_BASE}/{normalized_account_id}/locations",
            params={"pageSize": page_size, "readMask": "name,title,storeCode,locationKey,metadata"},
        )

        locations = resp.json().get("locations", [])
        normalized_locations: list[dict[str, Any]] = []
        account_prefix = normalized_account_id if normalized_account_id.startswith("accounts/") else f"accounts/{normalized_account_id}"
        for location in locations:
            item = dict(location)
            name = str(item.get("name", "")).strip().strip("/")
            if name.startswith("locations/"):
                item["name"] = f"{account_prefix}/{name}"
            elif name and not name.startswith("accounts/"):
                item["name"] = f"{account_prefix}/locations/{name}"
            normalized_locations.append(item)
        return normalized_locations

    # ── Reviews ───────────────────────────────────────────────────────────────

    def list_reviews(
        self, account_id: str, location_id: str, page_size: int = 10
    ) -> list[dict[str, Any]]:
        """List recent reviews for a location."""
        url = f"{_GBP_BASE}/{self._location_parent(account_id, location_id)}/reviews"
        resp = self._request("GET", url, params={"pageSize": page_size})
        return resp.json().get("reviews", [])

    def get_review(
        self, account_id: str, location_id: str, review_id: str
    ) -> dict[str, Any]:
        """Get a single review by review_id."""
        url = f"{_GBP_BASE}/{self._location_parent(account_id, location_id)}/reviews/{review_id}"
        resp = self._request("GET", url)
        return resp.json()

    def post_reply(
        self,
        account_id: str,
        location_id: str,
        review_id: str,
        reply_text: str,
    ) -> dict[str, Any]:
        """Post or update a reply to a review."""
        url = f"{_GBP_BASE}/{self._location_parent(account_id, location_id)}/reviews/{review_id}/reply"
        body = {"comment": reply_text}
        resp = self._request("PUT", url, content=json.dumps(body).encode())
        return resp.json()

    # ── Local Posts ──────────────────────────────────────────────────────────

    def create_local_post(
        self,
        account_id: str,
        location_id: str,
        summary: str,
        call_to_action_url: str = "",
    ) -> dict[str, Any]:
        """Create a Google Business local post (text only)."""
        url = f"{_GBP_BASE}/{self._location_parent(account_id, location_id)}/localPosts"
        body: dict[str, Any] = {
            "languageCode": "zh-TW",
            "summary": summary,
            "topicType": "STANDARD",
        }
        if call_to_action_url:
            body["callToAction"] = {
                "actionType": "LEARN_MORE",
                "url": call_to_action_url,
            }
        resp = self._request(
            "POST",
            url,
            content=json.dumps(body, ensure_ascii=False).encode(),
        )
        return resp.json()
