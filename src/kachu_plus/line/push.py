from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import httpx

from kachu_plus.config import get_settings


LINE_PUSH_URL = "https://api.line.me/v2/bot/message/push"
_PUSH_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_PUSH_COMPLETED_AT: dict[str, float] = {}


def _line_push_min_interval_seconds() -> float:
    raw = float(getattr(get_settings(), "LINE_PUSH_MIN_INTERVAL_SECONDS", 0.0) or 0.0)
    return max(raw, 0.0)


async def push_line_messages(*, to: str, messages: list[dict[str, Any]], access_token: str) -> None:
    body = {"to": to, "messages": messages}
    min_interval = _line_push_min_interval_seconds()
    if min_interval <= 0:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                LINE_PUSH_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(body, ensure_ascii=False).encode(),
                timeout=10.0,
            )
            response.raise_for_status()
        return

    lock = _PUSH_LOCKS.setdefault(access_token, asyncio.Lock())
    async with lock:
        last_completed_at = _LAST_PUSH_COMPLETED_AT.get(access_token)
        if last_completed_at is not None:
            remaining = min_interval - (time.monotonic() - last_completed_at)
            if remaining > 0:
                await asyncio.sleep(remaining)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                LINE_PUSH_URL,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                },
                content=json.dumps(body, ensure_ascii=False).encode(),
                timeout=10.0,
            )
            response.raise_for_status()
        _LAST_PUSH_COMPLETED_AT[access_token] = time.monotonic()


def text_message(text: str) -> dict[str, Any]:
    return {"type": "text", "text": text}


def meta_insights_report_message(
    *,
    period_label: str,
    summary: str,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    detail_contents = [
        {
            "type": "box",
            "layout": "baseline",
            "spacing": "sm",
            "contents": [
                {"type": "text", "text": str(item.get("label", "")), "size": "sm", "color": "#6B7280", "flex": 3, "wrap": True},
                {"type": "text", "text": str(item.get("value", "")), "size": "sm", "color": "#111827", "weight": "bold", "flex": 2, "align": "end", "wrap": True},
            ],
        }
        for item in details[:6]
        if item.get("label")
    ]
    if not detail_contents:
        detail_contents.append(
            {"type": "text", "text": "目前沒有可展示的明細數據。", "size": "sm", "color": "#6B7280", "wrap": True}
        )

    return {
        "type": "flex",
        "altText": f"Meta 成效報告｜{period_label}"[:120],
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "Meta 成效報告", "size": "sm", "color": "#6B7280"},
                    {"type": "text", "text": period_label, "weight": "bold", "size": "lg", "color": "#111827"},
                    {"type": "text", "text": summary, "size": "sm", "color": "#111827", "wrap": True},
                    {"type": "separator", "margin": "md"},
                    {"type": "box", "layout": "vertical", "spacing": "sm", "contents": detail_contents},
                ],
            },
        },
    }


def suggestion_card_message(
    *,
    suggestion_id: str,
    title: str,
    reason: str,
    suggested_action: str,
    draft_message: str,
    profile_count: int,
    expires_at: str,
) -> dict[str, Any]:
    summary = f"{title}｜{suggested_action}"[:120]
    affected_text = f"影響對象：{profile_count} 位" if profile_count > 0 else "影響對象：品牌層建議"
    return {
        "type": "flex",
        "altText": summary,
        "contents": {
            "type": "bubble",
            "body": {
                "type": "box",
                "layout": "vertical",
                "spacing": "md",
                "contents": [
                    {"type": "text", "text": "Kachu+ 主動建議", "size": "sm", "color": "#6B7280"},
                    {"type": "text", "text": title, "weight": "bold", "size": "lg", "wrap": True},
                    {"type": "text", "text": reason, "size": "sm", "color": "#374151", "wrap": True},
                    {"type": "separator"},
                    {"type": "text", "text": affected_text, "size": "sm", "color": "#111827", "wrap": True},
                    {"type": "text", "text": f"建議行動：{suggested_action}", "size": "sm", "color": "#111827", "wrap": True},
                    {"type": "text", "text": f"草稿：{draft_message}", "size": "sm", "color": "#111827", "wrap": True},
                    {"type": "text", "text": f"有效期限：{expires_at}", "size": "xs", "color": "#9CA3AF", "wrap": True},
                ],
            },
            "footer": {
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#2563EB",
                        "action": {
                            "type": "postback",
                            "label": "確認送出",
                            "data": f"action=suggestion_accept&suggestion_id={suggestion_id}",
                        },
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {
                            "type": "postback",
                            "label": "先不要",
                            "data": f"action=suggestion_dismiss&suggestion_id={suggestion_id}",
                        },
                    },
                ],
            },
        },
    }


def resolve_tenant_line_recipients(*, repo: Any, settings: Any, tenant_id: str) -> list[str]:
    recipients: list[str] = []

    recipient_lookup = getattr(repo, "get_notification_line_user_ids", None)
    if callable(recipient_lookup):
        recipient_ids = recipient_lookup(tenant_id)
        if isinstance(recipient_ids, str):
            recipient_ids = [recipient_ids]
        elif not isinstance(recipient_ids, (list, tuple, set)):
            recipient_ids = []
        for recipient_id in recipient_ids:
            normalized = str(recipient_id or "").strip()
            if normalized and normalized not in recipients:
                recipients.append(normalized)
        if recipients:
            return recipients

    owner_lookup = getattr(repo, "get_owner_line_user_ids", None)
    if callable(owner_lookup):
        owner_ids = owner_lookup(tenant_id)
        if isinstance(owner_ids, str):
            owner_ids = [owner_ids]
        elif not isinstance(owner_ids, (list, tuple, set)):
            owner_ids = []
        for owner_id in owner_ids:
            normalized = str(owner_id or "").strip()
            if normalized and normalized not in recipients:
                recipients.append(normalized)
        if recipients:
            return recipients

    legacy = str(getattr(settings, "LINE_BOSS_USER_ID", "") or "").strip()
    return [legacy] if legacy else []