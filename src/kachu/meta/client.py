"""
Meta Graph API client for Instagram and Facebook publishing, Insights, and comment management.

Scopes required (configured during OAuth):
  - instagram_basic, instagram_content_publish
  - pages_manage_posts, pages_read_engagement
  - read_insights                  (FB Page / Post Insights)
  - pages_manage_engagement        (reply / hide FB comments)
  - instagram_manage_comments      (reply / hide IG comments)

Reference:
  https://developers.facebook.com/docs/instagram-api/guides/content-publishing
  https://developers.facebook.com/docs/pages/publishing
  https://developers.facebook.com/docs/graph-api/reference/insights
  https://developers.facebook.com/docs/graph-api/reference/comment
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
        fb_access_token: str | None = None,
    ) -> None:
        self._token = access_token
        self._ig_user_id = ig_user_id
        self._fb_page_id = fb_page_id
        self._fb_token = fb_access_token or access_token

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
                    "access_token": self._fb_token,
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
                    "access_token": self._fb_token,
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

    # ── FB Page Insights ──────────────────────────────────────────────────────

    async def get_fb_page_insights(
        self,
        *,
        metric_names: list[str] | None = None,
        period: str = "day",
        since: str = "",
        until: str = "",
    ) -> dict[str, Any]:
        """
        Fetch FB Page Insights metrics.

        Requires ``read_insights`` scope.

        Args:
            metric_names: List of metric names, e.g. ``["page_impressions",
                "page_engaged_users"]``.  Defaults to a standard set.
            period: Aggregation period — ``day`` | ``week`` | ``days_28`` | ``month``.
            since: ISO-8601 date string for range start (optional).
            until: ISO-8601 date string for range end (optional).

        Returns:
            ``{"data": [{"name": ..., "period": ..., "values": [...], ...}, ...]}``
        """
        if not self._fb_page_id:
            raise MetaAPIError("fb_page_id not configured")

        if metric_names is None:
            metric_names = [
                "page_impressions",
                "page_impressions_unique",
                "page_engaged_users",
                "page_post_engagements",
                "page_fan_adds",
                "page_views_total",
            ]

        params: dict[str, Any] = {
            "metric": ",".join(metric_names),
            "period": period,
            "access_token": self._fb_token,
        }
        if since:
            params["since"] = since
        if until:
            params["until"] = until

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{self._fb_page_id}/insights",
                params=params,
            )
            self._raise_for_error(resp)

        return resp.json()

    # ── FB Post Insights ──────────────────────────────────────────────────────

    async def get_fb_post_insights(
        self,
        *,
        post_id: str,
        metric_names: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Fetch Insights for a specific FB Page post.

        Requires ``read_insights`` scope.

        Args:
            post_id: The Facebook post ID (e.g. ``940149472511909_123456``).
            metric_names: List of post-level metrics.  Defaults to a standard set.

        Returns:
            ``{"data": [{"name": ..., "values": [...], ...}, ...], "post_id": ...}``
        """
        if metric_names is None:
            metric_names = [
                "post_impressions",
                "post_impressions_unique",
                "post_engagements",
                "post_clicks",
                "post_reactions_by_type_total",
            ]

        params: dict[str, Any] = {
            "metric": ",".join(metric_names),
            "access_token": self._fb_token,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{post_id}/insights",
                params=params,
            )
            self._raise_for_error(resp)
            data = resp.json()

        data["post_id"] = post_id
        return data

    # ── FB Comment Management ─────────────────────────────────────────────────

    async def get_fb_comments(
        self,
        *,
        object_id: str,
        limit: int = 25,
    ) -> dict[str, Any]:
        """
        List top-level comments on a FB post or photo.

        Requires ``pages_read_engagement`` or ``pages_manage_engagement`` scope.

        Args:
            object_id: FB post/photo ID to list comments for.
            limit: Maximum number of comments to return (default 25, max 100).

        Returns:
            ``{"data": [{"id": ..., "message": ..., "from": {...}, ...}, ...]}``
        """
        params: dict[str, Any] = {
            "fields": "id,message,from,created_time,like_count,is_hidden",
            "limit": min(limit, 100),
            "access_token": self._fb_token,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{object_id}/comments",
                params=params,
            )
            self._raise_for_error(resp)

        return resp.json()

    async def reply_fb_comment(
        self,
        *,
        comment_id: str,
        message: str,
    ) -> dict[str, Any]:
        """
        Reply to a FB comment on behalf of the Page.

        Requires ``pages_manage_engagement`` scope.

        Args:
            comment_id: The comment to reply to.
            message: Reply text.

        Returns:
            ``{"id": "<new_comment_id>"}``
        """
        if not message:
            raise MetaAPIError("reply message cannot be empty")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{comment_id}/comments",
                params={
                    "message": message,
                    "access_token": self._fb_token,
                },
            )
            self._raise_for_error(resp)

        return resp.json()

    async def hide_fb_comment(
        self,
        *,
        comment_id: str,
        is_hidden: bool = True,
    ) -> dict[str, Any]:
        """
        Hide or unhide a comment on a FB Page post.

        Requires ``pages_manage_engagement`` scope.

        Args:
            comment_id: The comment to hide or unhide.
            is_hidden: ``True`` to hide, ``False`` to unhide.

        Returns:
            ``{"success": true}``
        """
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{comment_id}",
                params={
                    "is_hidden": str(is_hidden).lower(),
                    "access_token": self._fb_token,
                },
            )
            self._raise_for_error(resp)

        return resp.json()

    # ── IG Comment Management ─────────────────────────────────────────────────

    async def get_ig_comments(
        self,
        *,
        media_id: str,
        limit: int = 25,
    ) -> dict[str, Any]:
        """
        List comments on an IG media object.

        Requires ``instagram_manage_comments`` scope.

        Args:
            media_id: The IG media ID (e.g. the ``ig_media_id`` from ``post_ig_photo``).
            limit: Maximum number of comments to return (default 25, max 100).

        Returns:
            ``{"data": [{"id": ..., "text": ..., "timestamp": ..., "hidden": ...}, ...]}``
        """
        if not self._ig_user_id:
            raise MetaAPIError("ig_user_id not configured")

        params: dict[str, Any] = {
            "fields": "id,text,timestamp,username,hidden,like_count",
            "limit": min(limit, 100),
            "access_token": self._token,
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                f"{GRAPH_BASE}/{media_id}/comments",
                params=params,
            )
            self._raise_for_error(resp)

        return resp.json()

    async def reply_ig_comment(
        self,
        *,
        comment_id: str,
        message: str,
    ) -> dict[str, Any]:
        """
        Reply to a comment on an IG media object.

        Requires ``instagram_manage_comments`` scope.

        Args:
            comment_id: The IG comment ID to reply to.
            message: Reply text (do not include ``@username`` — the API adds it).

        Returns:
            ``{"id": "<new_comment_id>"}``
        """
        if not self._ig_user_id:
            raise MetaAPIError("ig_user_id not configured")
        if not message:
            raise MetaAPIError("reply message cannot be empty")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{self._ig_user_id}/replies",
                params={
                    "commented_media_id": comment_id,
                    "message": message,
                    "access_token": self._token,
                },
            )
            self._raise_for_error(resp)

        return resp.json()

    async def hide_ig_comment(
        self,
        *,
        comment_id: str,
        hide: bool = True,
    ) -> dict[str, Any]:
        """
        Hide or unhide an IG comment.

        Requires ``instagram_manage_comments`` scope.

        Args:
            comment_id: The IG comment ID.
            hide: ``True`` to hide, ``False`` to unhide.

        Returns:
            ``{"success": true}``
        """
        if not self._ig_user_id:
            raise MetaAPIError("ig_user_id not configured")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{GRAPH_BASE}/{comment_id}",
                params={
                    "hide": str(hide).lower(),
                    "access_token": self._token,
                },
            )
            self._raise_for_error(resp)

        return resp.json()
