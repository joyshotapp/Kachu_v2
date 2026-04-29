from __future__ import annotations

import json
import logging
from typing import Any

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
