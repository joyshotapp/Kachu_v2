from __future__ import annotations

from typing import Any


def build_photo_content_flex(
    run_id: str,
    tenant_id: str,
    drafts: dict[str, Any],
) -> dict[str, Any]:
    ig_fb_draft = drafts.get("ig_fb", "（草稿載入中）")
    google_draft = drafts.get("google", "（Google 商家版載入中）")

    approve_data = f"action=approve&run_id={run_id}&tenant_id={tenant_id}"
    schedule_data = f"action=schedule_publish&run_id={run_id}&tenant_id={tenant_id}"
    edit_data = f"action=edit&run_id={run_id}&tenant_id={tenant_id}"
    reject_data = f"action=reject&run_id={run_id}&tenant_id={tenant_id}"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "📸 新貼文草稿準備好了",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#1DB954",
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "【IG / Facebook 版】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {
                    "type": "text",
                    "text": str(ig_fb_draft)[:2000],
                    "wrap": True,
                    "size": "sm",
                },
                {"type": "separator"},
                {
                    "type": "text",
                    "text": "【Google 商家版】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {
                    "type": "text",
                    "text": str(google_draft)[:2000],
                    "wrap": True,
                    "size": "sm",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "🚀 立即發布",
                        "data": approve_data,
                        "displayText": "立即發布",
                    },
                    "style": "primary",
                    "color": "#1DB954",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "🗓️ 排程發布",
                        "data": schedule_data,
                        "displayText": "排程發布",
                    },
                    "style": "secondary",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "✏️ 我要修改",
                        "data": edit_data,
                        "displayText": "我要修改",
                    },
                    "style": "secondary",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "❌ 先不用",
                        "data": reject_data,
                        "displayText": "先不用",
                    },
                    "style": "secondary",
                },
            ],
        },
    }


def build_review_reply_flex(
    run_id: str,
    tenant_id: str,
    review_content: str,
    reply_draft: str,
) -> dict[str, Any]:
    approve_data = f"action=approve&run_id={run_id}&tenant_id={tenant_id}"
    edit_data = f"action=edit&run_id={run_id}&tenant_id={tenant_id}"
    reject_data = f"action=reject&run_id={run_id}&tenant_id={tenant_id}"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "⭐ 新評論回覆草稿",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#FF6B35",
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "【顧客評論】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {
                    "type": "text",
                    "text": (review_content[:240] + ("…" if len(review_content) > 240 else "")) or "（無評論內容）",
                    "wrap": True,
                    "size": "sm",
                },
                {"type": "separator"},
                {
                    "type": "text",
                    "text": "【建議回覆】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {
                    "type": "text",
                    "text": (reply_draft[:240] + ("…" if len(reply_draft) > 240 else "")) or "（草稿生成中）",
                    "wrap": True,
                    "size": "sm",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "✅ 確認回覆",
                        "data": approve_data,
                        "displayText": "確認回覆",
                    },
                    "style": "primary",
                    "color": "#FF6B35",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "✏️ 我要修改",
                        "data": edit_data,
                        "displayText": "我要修改",
                    },
                    "style": "secondary",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "❌ 先不用",
                        "data": reject_data,
                        "displayText": "先不用",
                    },
                    "style": "secondary",
                },
            ],
        },
    }


def build_knowledge_update_flex(
    run_id: str,
    tenant_id: str,
    drafts: dict[str, Any],
) -> dict[str, Any]:
    diff_summary = drafts.get("diff_summary", "（無摘要）")
    parsed = drafts.get("parsed_update", {})
    new_value = parsed.get("new_value", "")
    field = parsed.get("field") or parsed.get("subject", "")

    approve_data = f"action=approve&run_id={run_id}&tenant_id={tenant_id}"
    reject_data = f"action=reject&run_id={run_id}&tenant_id={tenant_id}"

    body_contents: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": "【變更摘要】",
            "weight": "bold",
            "size": "sm",
            "color": "#555555",
        },
        {
            "type": "text",
            "text": diff_summary[:300] + ("…" if len(diff_summary) > 300 else ""),
            "wrap": True,
            "size": "sm",
        },
    ]
    if field and new_value:
        body_contents += [
            {"type": "separator"},
            {
                "type": "text",
                "text": f"【新內容 — {field}】",
                "weight": "bold",
                "size": "sm",
                "color": "#555555",
            },
            {
                "type": "text",
                "text": str(new_value)[:240] + ("…" if len(str(new_value)) > 240 else ""),
                "wrap": True,
                "size": "sm",
            },
        ]

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "📝 知識庫更新確認",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#4A90D9",
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "✅ 確認更新",
                        "data": approve_data,
                        "displayText": "確認更新",
                    },
                    "style": "primary",
                    "color": "#4A90D9",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "❌ 取消",
                        "data": reject_data,
                        "displayText": "取消",
                    },
                    "style": "secondary",
                },
            ],
        },
    }


def build_google_post_flex(
    run_id: str,
    tenant_id: str,
    post_text: str,
    *,
    title: str = "📊 Google 商家動態草稿",
    accent_color: str = "#34A853",
) -> dict[str, Any]:
    approve_data = f"action=approve&run_id={run_id}&tenant_id={tenant_id}"
    schedule_data = f"action=schedule_publish&run_id={run_id}&tenant_id={tenant_id}"
    edit_data = f"action=edit&run_id={run_id}&tenant_id={tenant_id}"
    reject_data = f"action=reject&run_id={run_id}&tenant_id={tenant_id}"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": accent_color,
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "【貼文草稿】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {
                    "type": "text",
                    "text": post_text[:480] + ("…" if len(post_text) > 480 else ""),
                    "wrap": True,
                    "size": "sm",
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "🚀 立即發布",
                        "data": approve_data,
                        "displayText": "立即發布",
                    },
                    "style": "primary",
                    "color": accent_color,
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "🗓️ 排程發布",
                        "data": schedule_data,
                        "displayText": "排程發布",
                    },
                    "style": "secondary",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "✏️ 我要修改",
                        "data": edit_data,
                        "displayText": "我要修改",
                    },
                    "style": "secondary",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "❌ 先不用",
                        "data": reject_data,
                        "displayText": "先不用",
                    },
                    "style": "secondary",
                },
            ],
        },
    }


def build_planned_content_flex(
    run_id: str,
    tenant_id: str,
    drafts: dict[str, Any],
) -> dict[str, Any]:
    ig_fb_draft = drafts.get("ig_fb", "（IG / FB 草稿載入中）")
    google_draft = drafts.get("google", "（Google 商家版載入中）")
    approve_data = f"action=approve&run_id={run_id}&tenant_id={tenant_id}"
    edit_data = f"action=edit&run_id={run_id}&tenant_id={tenant_id}"
    reject_data = f"action=reject&run_id={run_id}&tenant_id={tenant_id}"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "🗂️ 企劃排程草稿",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#2A6F97",
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {
                    "type": "text",
                    "text": "【IG / Facebook 版】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {"type": "text", "text": str(ig_fb_draft)[:2000], "wrap": True, "size": "sm"},
                {"type": "separator"},
                {
                    "type": "text",
                    "text": "【Google 商家版】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {"type": "text", "text": str(google_draft)[:2000], "wrap": True, "size": "sm"},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "action": {"type": "postback", "label": "🚀 立即發布", "data": approve_data, "displayText": "立即發布"},
                    "style": "primary",
                    "color": "#2A6F97",
                },
                {
                    "type": "button",
                    "action": {"type": "postback", "label": "✏️ 我要修改", "data": edit_data, "displayText": "我要修改"},
                    "style": "secondary",
                },
                {
                    "type": "button",
                    "action": {"type": "postback", "label": "❌ 先不用", "data": reject_data, "displayText": "先不用"},
                    "style": "secondary",
                },
            ],
        },
    }


def build_external_reply_flex(
    run_id: str,
    tenant_id: str,
    *,
    source_label: str,
    customer_name: str,
    incoming_text: str,
    reply_draft: str,
) -> dict[str, Any]:
    approve_data = f"action=approve&run_id={run_id}&tenant_id={tenant_id}"
    edit_data = f"action=edit&run_id={run_id}&tenant_id={tenant_id}"
    reject_data = f"action=reject&run_id={run_id}&tenant_id={tenant_id}"
    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": f"💬 {source_label} 回覆草稿", "weight": "bold", "size": "lg", "color": "#ffffff"}
            ],
            "backgroundColor": "#C94F2D",
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": f"【對方】{customer_name or '顧客'}", "weight": "bold", "size": "sm", "color": "#555555"},
                {"type": "text", "text": incoming_text[:240] + ("…" if len(incoming_text) > 240 else ""), "wrap": True, "size": "sm"},
                {"type": "separator"},
                {"type": "text", "text": "【建議回覆】", "weight": "bold", "size": "sm", "color": "#555555"},
                {"type": "text", "text": reply_draft[:240] + ("…" if len(reply_draft) > 240 else ""), "wrap": True, "size": "sm"},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {"type": "button", "action": {"type": "postback", "label": "✅ 送出回覆", "data": approve_data, "displayText": "送出回覆"}, "style": "primary", "color": "#C94F2D"},
                {"type": "button", "action": {"type": "postback", "label": "✏️ 我要修改", "data": edit_data, "displayText": "我要修改"}, "style": "secondary"},
                {"type": "button", "action": {"type": "postback", "label": "❌ 先不用", "data": reject_data, "displayText": "先不用"}, "style": "secondary"},
            ],
        },
    }