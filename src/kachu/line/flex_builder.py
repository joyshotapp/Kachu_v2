from __future__ import annotations

from typing import Any


def build_photo_content_flex(
    run_id: str,
    tenant_id: str,
    drafts: dict[str, Any],
) -> dict[str, Any]:
    """Build LINE Flex Message bubble for photo content approval."""
    ig_fb_draft = drafts.get("ig_fb", "（草稿載入中）")
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
                    "text": ig_fb_draft[:240] + ("…" if len(ig_fb_draft) > 240 else ""),
                    "wrap": True,
                    "size": "sm",
                },
                {
                    "type": "separator",
                },
                {
                    "type": "text",
                    "text": "【Google 商家版】",
                    "weight": "bold",
                    "size": "sm",
                    "color": "#555555",
                },
                {
                    "type": "text",
                    "text": google_draft[:240] + ("…" if len(google_draft) > 240 else ""),
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
                        "label": "✅ 確認發布",
                        "data": approve_data,
                        "displayText": "確認發布",
                    },
                    "style": "primary",
                    "color": "#1DB954",
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
    """Build LINE Flex Message bubble for review reply approval."""
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
                    "text": review_content[:240] + ("…" if len(review_content) > 240 else ""),
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
                    "text": reply_draft[:240] + ("…" if len(reply_draft) > 240 else ""),
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
    """Build LINE Flex Message bubble for knowledge-update approval (approve / reject only)."""
    diff_summary = drafts.get("diff_summary", "（無摘要）")
    parsed = drafts.get("parsed_update", {})
    new_value = parsed.get("new_value", "")
    field = parsed.get("field", "")

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
) -> dict[str, Any]:
    """Build LINE Flex Message bubble for Google Business Post approval."""
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
                    "text": "📊 Google 商家動態草稿",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#34A853",
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
                        "label": "✅ 確認發布",
                        "data": approve_data,
                        "displayText": "確認發布",
                    },
                    "style": "primary",
                    "color": "#34A853",
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


def build_ga4_report_flex(
    run_id: str,
    tenant_id: str,
    insights: dict[str, Any],
) -> dict[str, Any]:
    """Build LINE Flex Message bubble for GA4 weekly report with Google Post CTA button."""
    summary = insights.get("summary", "本週流量報告")
    highlights: list[str] = insights.get("highlights", [])[:3]
    actions: list[str] = insights.get("actions", [])[:2]

    highlight_contents: list[dict[str, Any]] = []
    for h in highlights:
        highlight_contents.append({
            "type": "text",
            "text": f"• {h}",
            "wrap": True,
            "size": "sm",
            "color": "#555555",
        })

    action_contents: list[dict[str, Any]] = []
    if actions:
        action_contents.append({"type": "separator", "margin": "md"})
        action_contents.append({
            "type": "text",
            "text": "💡 快速行動建議",
            "weight": "bold",
            "size": "sm",
            "margin": "md",
        })
        for a in actions:
            action_contents.append({
                "type": "text",
                "text": f"→ {a}",
                "wrap": True,
                "size": "sm",
                "color": "#1a73e8",
            })

    trigger_data = f"action=trigger_workflow&workflow=kachu_google_post&tenant_id={tenant_id}"

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "📊 本週 GA4 週報",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#1a73e8",
            "paddingAll": "16px",
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": summary,
                    "wrap": True,
                    "weight": "bold",
                    "size": "md",
                },
                {"type": "separator", "margin": "md"},
                {
                    "type": "text",
                    "text": "📈 本週亮點",
                    "weight": "bold",
                    "size": "sm",
                    "margin": "md",
                },
                *highlight_contents,
                *action_contents,
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
                        "label": "要我幫你更新 Google 商家動態嗎？",
                        "data": trigger_data,
                        "displayText": "好，幫我更新 Google 商家動態",
                    },
                    "style": "primary",
                    "color": "#34A853",
                },
            ],
        },
    }