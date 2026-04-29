"""
Meta Graph API client for Instagram and Facebook publishing.

Scope required (configured during OAuth):
  - instagram_basic, instagram_content_publish
  - pages_manage_posts, pages_read_engagement

Reference:
  https://developers.facebook.com/docs/instagram-api/guides/content-publishing
  https://developers.facebook.com/docs/pages/publishing
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"


class MetaAPIError(Exception):
    """Raised when Meta Graph API returns an error."""

    def __init__(self, message: str, code: int | None = None) -> None:
        self.code = code
        super().__init__(message)


class MetaClient:
    """
    Thin async wrapper around Meta Graph API for IG + FB post publishing.

    Usage:
        client = MetaClient(ig_user_id="...", fb_page_id="...", access_token="...")
        result = await client.post_photo(image_url="...", caption="...")
    """

    def __init__(
        self,
        access_token: str,
        ig_user_id: str | None = None,
        fb_page_id: str | None = None,
    ) -> None:
        self._token = access_token
        self._ig_user_id = ig_user_id
        self._fb_page_id = fb_page_id

    # ── Instagram Content Publishing ─────────────────────────────────────────

    async def post_ig_photo(self, *, image_url: str, caption: str) -> dict[str, Any]:
        """
        Publish a photo to Instagram via two-step container creation + publish.
        Returns {"creation_id": "...", "ig_media_id": "..."} on success.
        """
        if not self._ig_user_id:
            raise MetaAPIError("ig_user_id not configured")

        # Step 1: Create media container
        async with httpx.AsyncClient(timeout=30.0) as client:
            container_resp = await client.post(
                f"{GRAPH_BASE}/{self._ig_user_id}/media",
                params={
                    "image_url": image_url,
                    "caption": caption,
                    "access_token": self._token,
                },
            )
            self._raise_for_error(container_resp)
            container_data = container_resp.json()
            creation_id = container_data.get("id")
            if not creation_id:
                raise MetaAPIError(f"IG container creation returned no id: {container_data}")

            # Step 2: Publish the container
            publish_resp = await client.post(
                f"{GRAPH_BASE}/{self._ig_user_id}/media_publish",
                params={
                    "creation_id": creation_id,
                    "access_token": self._token,
                },
            )
            self._raise_for_error(publish_resp)
            publish_data = publish_resp.json()

        return {"creation_id": creation_id, "ig_media_id": publish_data.get("id")}

    async def post_ig_text(self, *, caption: str) -> dict[str, Any]:
        """
        Publish a text-only Instagram post (IG text via Reels-free fallback).
        NOTE: Instagram does not support text-only feed posts via API — this
        publishes a caption without a media asset, which is rejected by the API.
        In practice callers should always provide an image_url; this method is
        provided as a fallback that returns a 'skipped' response.
        """
        logger.warning(
            "Instagram does not support text-only posts via the Content Publishing API; skipping."
        )
        return {"status": "skipped", "reason": "instagram_text_only_not_supported"}

    # ── Facebook Page Publishing ──────────────────────────────────────────────

    async def post_fb_photo(self, *, image_url: str, message: str) -> dict[str, Any]:
        """
        Publish a photo post to a Facebook Page.
        Returns {"fb_post_id": "..."} on success.
        """
        if not self._fb_page_id:
            raise MetaAPIError("fb_page_id not configured")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{self._fb_page_id}/photos",
                params={
                    "url": image_url,
                    "message": message,
                    "access_token": self._token,
                },
            )
            self._raise_for_error(resp)
            data = resp.json()

        return {"fb_post_id": data.get("post_id") or data.get("id")}

    async def post_fb_text(self, *, message: str) -> dict[str, Any]:
        """
        Publish a text-only post to a Facebook Page feed.
        Returns {"fb_post_id": "..."} on success.
        """
        if not self._fb_page_id:
            raise MetaAPIError("fb_page_id not configured")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{self._fb_page_id}/feed",
                params={
                    "message": message,
                    "access_token": self._token,
                },
            )
            self._raise_for_error(resp)
            data = resp.json()

        return {"fb_post_id": data.get("id")}

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _raise_for_error(resp: httpx.Response) -> None:
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                body = exc.response.json()
                err = body.get("error", {})
                raise MetaAPIError(
                    err.get("message", str(exc)), code=err.get("code")
                ) from exc
            except (KeyError, ValueError):
                raise MetaAPIError(str(exc)) from exc
