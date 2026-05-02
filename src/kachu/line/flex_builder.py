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
                    "text": ig_fb_draft[:2000],
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
                    "text": google_draft[:2000],
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


def build_business_profile_update_flex(
    run_id: str,
    tenant_id: str,
    drafts: dict[str, Any],
) -> dict[str, Any]:
    """Build LINE Flex Message bubble for business-profile update approval."""
    diff_summary = drafts.get("diff_summary", "（無摘要）")
    parsed = drafts.get("parsed_update", {})
    new_value = parsed.get("new_value", "")
    field = parsed.get("field", "")
    effective_date = parsed.get("effective_date", "")
    followup_hint = parsed.get("followup_hint", "")

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
    if effective_date:
        body_contents += [
            {"type": "separator"},
            {
                "type": "text",
                "text": f"生效日期：{effective_date}",
                "wrap": True,
                "size": "sm",
                "color": "#666666",
            },
        ]
    if followup_hint:
        body_contents.append(
            {
                "type": "text",
                "text": followup_hint[:200] + ("…" if len(followup_hint) > 200 else ""),
                "wrap": True,
                "size": "sm",
                "color": "#666666",
            }
        )

    return {
        "type": "bubble",
        "size": "mega",
        "header": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "text",
                    "text": "🏪 營業資訊更新確認",
                    "weight": "bold",
                    "size": "lg",
                    "color": "#ffffff",
                }
            ],
            "backgroundColor": "#2F855A",
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
                    "color": "#2F855A",
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


def build_google_post_flex(
    run_id: str,
    tenant_id: str,
    post_text: str,
    *,
    title: str = "📊 Google 商家動態草稿",
    accent_color: str = "#34A853",
) -> dict[str, Any]:
    """Build LINE Flex Message bubble for Google Business Post approval."""
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


def build_meta_post_flex(
    run_id: str,
    tenant_id: str,
    post_text: str,
) -> dict[str, Any]:
    """Build LINE Flex Message bubble for Meta scheduled post approval."""
    return build_google_post_flex(
        run_id=run_id,
        tenant_id=tenant_id,
        post_text=post_text,
        title="📣 Meta 排程發文草稿",
        accent_color="#1877F2",
    )


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


# ── Flow A: Meta Insights on-demand report ────────────────────────────────────

def build_meta_insights_flex(
    tenant_id: str,
    summary: str,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    """Flex bubble for Meta (FB/IG) page insights on-demand report."""
    detail_contents: list[dict[str, Any]] = []
    for item in details[:8]:
        detail_contents.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": item.get("label", ""), "size": "sm", "color": "#555555", "flex": 3},
                {"type": "text", "text": str(item.get("value", "")), "size": "sm", "color": "#111111", "align": "end", "flex": 2},
            ],
        })

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1877F2",
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": "📊 Facebook 成效報告", "color": "#FFFFFF", "weight": "bold", "size": "md"},
                {"type": "text", "text": "過去 7 天", "color": "#DDDDFF", "size": "sm"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": summary, "wrap": True, "size": "sm", "color": "#333333"},
                {"type": "separator"},
                *detail_contents,
            ],
        },
    }


# ── Flow B: Post performance report (24h after publish) ──────────────────────

def build_post_performance_flex(
    tenant_id: str,
    fb_post_id: str,
    summary: str,
    details: list[dict[str, Any]],
) -> dict[str, Any]:
    """Flex bubble for post performance auto-report ~24h after publishing."""
    detail_contents: list[dict[str, Any]] = []
    for item in details[:6]:
        detail_contents.append({
            "type": "box",
            "layout": "horizontal",
            "contents": [
                {"type": "text", "text": item.get("label", ""), "size": "sm", "color": "#555555", "flex": 3},
                {"type": "text", "text": str(item.get("value", "")), "size": "sm", "color": "#111111", "align": "end", "flex": 2},
            ],
        })

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#1877F2",
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": "📈 貼文成效回報（發文後 24h）", "color": "#FFFFFF", "weight": "bold", "size": "md"},
                {"type": "text", "text": f"貼文 ID: {fb_post_id}", "color": "#CCDDFF", "size": "xs"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "contents": [
                {"type": "text", "text": summary, "wrap": True, "size": "sm", "color": "#333333"},
                {"type": "separator"},
                *detail_contents,
            ],
        },
    }


# ── Flow C: Comment notification with reply/hide buttons ─────────────────────

def build_comment_notify_flex(
    *,
    tenant_id: str,
    comment_id: str,
    comment_author: str,
    comment_text: str,
    reply_draft: str,
    platform: str,  # "fb" or "ig"
    object_id: str,  # fb_post_id or ig_media_id
) -> dict[str, Any]:
    """Flex bubble to notify boss of a new comment and offer approve/edit/hide."""
    platform_label = "Facebook" if platform == "fb" else "Instagram"
    platform_color = "#1877F2" if platform == "fb" else "#C13584"

    approve_data = (
        f"action=reply_comment&platform={platform}&comment_id={comment_id}"
        f"&tenant_id={tenant_id}&object_id={object_id}"
    )
    hide_data = (
        f"action=hide_comment&platform={platform}&comment_id={comment_id}"
        f"&tenant_id={tenant_id}&object_id={object_id}"
    )

    return {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": platform_color,
            "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": f"💬 新 {platform_label} 留言", "color": "#FFFFFF", "weight": "bold", "size": "md"},
                {"type": "text", "text": comment_author, "color": "#DDEEFF", "size": "xs"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "paddingAll": "16px",
            "contents": [
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#F5F5F5",
                    "paddingAll": "10px",
                    "cornerRadius": "6px",
                    "contents": [
                        {"type": "text", "text": comment_text[:200], "wrap": True, "size": "sm", "color": "#333333"},
                    ],
                },
                {"type": "text", "text": "AI 建議回覆：", "size": "xs", "color": "#888888", "margin": "md"},
                {
                    "type": "box",
                    "layout": "vertical",
                    "backgroundColor": "#EFF8FF",
                    "paddingAll": "10px",
                    "cornerRadius": "6px",
                    "contents": [
                        {"type": "text", "text": reply_draft[:200], "wrap": True, "size": "sm", "color": "#1877F2"},
                    ],
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "12px",
            "contents": [
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "✅ 確認回覆",
                        "data": approve_data,
                        "displayText": "確認送出這則回覆",
                    },
                    "style": "primary",
                    "color": "#1877F2",
                    "height": "sm",
                },
                {
                    "type": "button",
                    "action": {
                        "type": "postback",
                        "label": "🙈 隱藏留言",
                        "data": hide_data,
                        "displayText": "隱藏這則留言",
                    },
                    "style": "secondary",
                    "height": "sm",
                },
            ],
        },
    }