from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# Google Business Profile API v4
_GBP_BASE = "https://mybusiness.googleapis.com/v4"
_MY_BUSINESS_BASE = "https://mybusinessaccountmanagement.googleapis.com/v1"

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


class GoogleBusinessClient:
    """
    Client for Google Business Profile API.
    Handles review fetching and reply posting.
    """

    def __init__(self, service_account_json_path: str) -> None:
        self._creds = _build_credentials(service_account_json_path)

    def _headers(self) -> dict[str, str]:
        token = _get_token(self._creds)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ── Reviews ───────────────────────────────────────────────────────────────

    def list_reviews(
        self, account_id: str, location_id: str, page_size: int = 10
    ) -> list[dict[str, Any]]:
        """List recent reviews for a location."""
        import httpx

        url = f"{_GBP_BASE}/{account_id}/{location_id}/reviews"
        resp = httpx.get(
            url,
            headers=self._headers(),
            params={"pageSize": page_size},
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json().get("reviews", [])

    def get_review(
        self, account_id: str, location_id: str, review_id: str
    ) -> dict[str, Any]:
        """Get a single review by review_id."""
        import httpx

        url = f"{_GBP_BASE}/{account_id}/{location_id}/reviews/{review_id}"
        resp = httpx.get(url, headers=self._headers(), timeout=15.0)
        resp.raise_for_status()
        return resp.json()

    def post_reply(
        self,
        account_id: str,
        location_id: str,
        review_id: str,
        reply_text: str,
    ) -> dict[str, Any]:
        """Post or update a reply to a review."""
        import httpx

        url = f"{_GBP_BASE}/{account_id}/{location_id}/reviews/{review_id}/reply"
        body = {"comment": reply_text}
        resp = httpx.put(
            url,
            headers=self._headers(),
            content=json.dumps(body).encode(),
            timeout=15.0,
        )
        resp.raise_for_status()
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
        import httpx

        url = f"{_GBP_BASE}/{account_id}/{location_id}/localPosts"
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
        resp = httpx.post(
            url,
            headers=self._headers(),
            content=json.dumps(body, ensure_ascii=False).encode(),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()
