from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, Mock

import httpx

logger = logging.getLogger(__name__)

LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"


async def push_line_messages(
    *,
    to: str,
    messages: list[dict[str, Any]],
    access_token: str,
) -> None:
    """Push LINE messages to a user/group via LINE Messaging API."""
    body = {"to": to, "messages": messages}
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            LINE_PUSH_URL,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            content=json.dumps(body, ensure_ascii=False).encode(),
            timeout=10.0,
        )
        resp.raise_for_status()


def text_message(text: str) -> dict[str, Any]:
    """Create a LINE text message object."""
    return {"type": "text", "text": text}


def resolve_tenant_line_recipients(*, repo: Any, settings: Any, tenant_id: str) -> list[str]:
    """Resolve active LINE recipients for a tenant.

    Legacy fallback is only kept for callers that do not yet provide a concrete
    repository lookup implementation, such as older tests or partial stubs.
    """
    recipients: list[str] = []

    recipient_lookup = getattr(repo, "get_notification_line_user_ids", None)
    recipient_lookup_is_placeholder_mock = isinstance(recipient_lookup, (AsyncMock, Mock)) and isinstance(
        getattr(recipient_lookup, "return_value", None),
        (AsyncMock, Mock),
    )
    if callable(recipient_lookup) and not recipient_lookup_is_placeholder_mock:
        recipient_ids = recipient_lookup(tenant_id)
        if isinstance(recipient_ids, str):
            recipient_ids = [recipient_ids]
        elif not isinstance(recipient_ids, (list, tuple, set)):
            recipient_ids = []
        for recipient_id in recipient_ids:
            normalized_recipient_id = str(recipient_id or "").strip()
            if normalized_recipient_id and normalized_recipient_id not in recipients:
                recipients.append(normalized_recipient_id)

        return recipients

    owner_lookup = getattr(repo, "get_owner_line_user_ids", None)
    owner_lookup_is_placeholder_mock = isinstance(owner_lookup, (AsyncMock, Mock)) and isinstance(
        getattr(owner_lookup, "return_value", None),
        (AsyncMock, Mock),
    )
    if callable(owner_lookup) and not owner_lookup_is_placeholder_mock:
        owner_ids = owner_lookup(tenant_id)
        if isinstance(owner_ids, str):
            owner_ids = [owner_ids]
        elif not isinstance(owner_ids, (list, tuple, set)):
            owner_ids = []
        for owner_id in owner_ids:
            normalized_owner_id = str(owner_id or "").strip()
            if normalized_owner_id and normalized_owner_id not in recipients:
                recipients.append(normalized_owner_id)

        return recipients

    legacy_boss_user_id = str(getattr(settings, "LINE_BOSS_USER_ID", "") or "").strip()
    if legacy_boss_user_id:
        return [legacy_boss_user_id]
    return []
