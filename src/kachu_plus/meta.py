from __future__ import annotations

from datetime import datetime, timedelta, timezone
from html import escape
import json
import logging
import secrets
import time
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from pydantic import BaseModel

from kachu_plus.config import Settings
from kachu_plus.line.flex_builder import build_external_reply_flex
from kachu_plus.line.push import meta_insights_report_message, push_line_messages, resolve_tenant_line_recipients, text_message

router = APIRouter(tags=["meta", "tools"])

GRAPH_API_VERSION = "v21.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_API_VERSION}"
META_OAUTH_BASE = f"https://www.facebook.com/{GRAPH_API_VERSION}/dialog/oauth"

logger = logging.getLogger(__name__)


class MetaConnectorError(ValueError):
    pass


class MetaGraphClient:
    def __init__(
        self,
        *,
        access_token: str = "",
        fb_page_id: str = "",
        ig_user_id: str = "",
        fb_access_token: str = "",
    ) -> None:
        self._access_token = access_token.strip()
        self._fb_page_id = fb_page_id.strip()
        self._ig_user_id = ig_user_id.strip()
        self._fb_access_token = (fb_access_token or access_token).strip()

    def _request(self, path: str, *, params: dict[str, Any], use_fb_token: bool = False) -> dict[str, Any]:
        token = self._fb_access_token if use_fb_token else self._access_token
        response = httpx.get(
            f"{GRAPH_BASE}/{path.lstrip('/')}",
            params={**params, "access_token": token},
            timeout=15.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                payload = response.json()
            except ValueError:
                raise MetaConnectorError(str(exc)) from exc
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise MetaConnectorError(str(error.get("message") or exc)) from exc
        return response.json()

    def _post(self, path: str, *, params: dict[str, Any], use_fb_token: bool = False) -> dict[str, Any]:
        token = self._fb_access_token if use_fb_token else self._access_token
        response = httpx.post(
            f"{GRAPH_BASE}/{path.lstrip('/')}",
            params={**params, "access_token": token},
            timeout=30.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            try:
                payload = response.json()
            except ValueError:
                raise MetaConnectorError(str(exc)) from exc
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            raise MetaConnectorError(str(error.get("message") or exc)) from exc
        return response.json()

    def post_ig_photo(self, *, image_url: str, caption: str) -> dict[str, Any]:
        if not self._ig_user_id:
            raise MetaConnectorError("meta connector missing ig_user_id")
        container = self._post(
            f"{self._ig_user_id}/media",
            params={"image_url": image_url, "caption": caption},
        )
        creation_id = str(container.get("id", "") or "")
        if not creation_id:
            raise MetaConnectorError("instagram media container id missing")
        published = self._post(
            f"{self._ig_user_id}/media_publish",
            params={"creation_id": creation_id},
        )
        return {"creation_id": creation_id, "ig_media_id": published.get("id", "")}

    def post_ig_text(self, *, caption: str) -> dict[str, Any]:
        logger.warning("Instagram does not support text-only posts via API; skipping")
        return {"status": "skipped", "reason": "instagram_text_only_not_supported", "caption": caption}

    def post_fb_photo(self, *, image_url: str, message: str) -> dict[str, Any]:
        if not self._fb_page_id:
            raise MetaConnectorError("meta connector missing fb_page_id")
        payload = self._post(
            f"{self._fb_page_id}/photos",
            params={"url": image_url, "message": message},
            use_fb_token=True,
        )
        return {"fb_post_id": payload.get("post_id") or payload.get("id") or ""}

    def post_fb_text(self, *, message: str) -> dict[str, Any]:
        if not self._fb_page_id:
            raise MetaConnectorError("meta connector missing fb_page_id")
        payload = self._post(
            f"{self._fb_page_id}/feed",
            params={"message": message},
            use_fb_token=True,
        )
        return {"fb_post_id": payload.get("id") or ""}

    def reply_to_comment(self, *, comment_id: str, message: str) -> dict[str, Any]:
        if not comment_id.strip():
            raise MetaConnectorError("meta comment id is required")
        payload = self._post(
            f"{comment_id}/comments",
            params={"message": message},
            use_fb_token=True,
        )
        return {"comment_id": payload.get("id") or ""}

    def send_page_message(self, *, recipient_id: str, message: str) -> dict[str, Any]:
        if not self._fb_page_id:
            raise MetaConnectorError("meta connector missing fb_page_id")
        if not recipient_id.strip():
            raise MetaConnectorError("meta recipient id is required")
        payload = self._post(
            f"{self._fb_page_id}/messages",
            params={"recipient": json.dumps({"id": recipient_id}, ensure_ascii=False), "message": json.dumps({"text": message}, ensure_ascii=False)},
            use_fb_token=True,
        )
        return {"message_id": payload.get("message_id") or payload.get("id") or ""}

    def get_fb_page_insights(self, *, period: str = "week", metric_names: list[str] | None = None) -> dict[str, Any]:
        if not self._fb_page_id:
            raise MetaConnectorError("meta connector missing fb_page_id")
        metrics = metric_names or [
            "page_impressions_unique",
            "page_post_engagements",
            "page_views_total",
            "page_total_actions",
        ]
        return self._request(
            f"{self._fb_page_id}/insights",
            params={"metric": ",".join(metrics), "period": period},
            use_fb_token=True,
        )

    def get_fb_post_insights(self, *, post_id: str, metric_names: list[str] | None = None) -> dict[str, Any]:
        if not post_id.strip():
            raise MetaConnectorError("fb post id is required")
        metrics = metric_names or [
            "post_impressions",
            "post_impressions_unique",
            "post_engagements",
            "post_clicks",
        ]
        return self._request(
            f"{post_id}/insights",
            params={"metric": ",".join(metrics)},
            use_fb_token=True,
        )

    def get_ig_media_insights(self, *, media_id: str, metric_names: list[str] | None = None) -> dict[str, Any]:
        if not media_id.strip():
            raise MetaConnectorError("instagram media id is required")
        metrics = metric_names or ["impressions", "reach", "saved"]
        return self._request(
            f"{media_id}/insights",
            params={"metric": ",".join(metrics)},
        )


class MetaConnectorRequest(BaseModel):
    account_label: str = "Meta"
    access_token: str
    fb_page_id: str = ""
    ig_user_id: str = ""
    fb_access_token: str = ""
    expires_at: int | None = None
    recent_fb_post_id: str = ""
    recent_ig_media_id: str = ""


class FetchMetaInsightsRequest(BaseModel):
    tenant_id: str
    period: str = "week"
    fb_post_id: str = ""
    ig_media_id: str = ""


class GenerateMetaInsightsSummaryRequest(BaseModel):
    tenant_id: str
    insights: dict[str, Any]


class SendMetaInsightsReportRequest(BaseModel):
    tenant_id: str
    summary: str
    details: list[dict[str, Any]] = []
    period: str = "week"
    recipient_line_ids: list[str] = []


class MetaConnectStartRequest(BaseModel):
    line_user_id: str = ""


class MetaPageSelectionRequest(BaseModel):
    page_id: str
    overwrite_existing: bool = False


class MetaDisconnectRequest(BaseModel):
    line_user_id: str = ""


def load_connector_credentials(account: Any) -> dict[str, Any]:
    raw = getattr(account, "credentials_json", "") or "{}"
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def resolve_meta_graph_client(
    *,
    repo: Any,
    tenant_id: str,
    client_factory: Callable[..., MetaGraphClient] | None = None,
) -> tuple[MetaGraphClient, dict[str, Any]]:
    account = repo.get_connector_account(tenant_id, "meta")
    if account is None:
        raise MetaConnectorError("meta connector is not configured")

    credentials = load_connector_credentials(account)
    access_token = str(credentials.get("access_token", "") or "").strip()
    if not access_token:
        raise MetaConnectorError("meta connector access token is missing")

    expires_at = credentials.get("expires_at")
    if expires_at is not None:
        try:
            expires_at_int = int(expires_at)
        except (TypeError, ValueError):
            expires_at_int = None
        if expires_at_int is not None and expires_at_int <= int(time.time()):
            raise MetaConnectorError("meta connector credential expired")

    factory = client_factory or MetaGraphClient
    return (
        factory(
            access_token=access_token,
            fb_page_id=str(credentials.get("fb_page_id", "") or ""),
            ig_user_id=str(credentials.get("ig_user_id", "") or ""),
            fb_access_token=str(credentials.get("fb_access_token", "") or ""),
        ),
        credentials,
    )


def flatten_insights_payload(payload: dict[str, Any]) -> dict[str, Any]:
    flattened: dict[str, Any] = {}
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        values = item.get("values", [])
        if not isinstance(values, list) or not values:
            continue
        latest = values[-1]
        if not isinstance(latest, dict):
            continue
        value = latest.get("value")
        if isinstance(value, dict):
            flattened[name] = json.dumps(value, ensure_ascii=False)
        elif value is not None:
            flattened[name] = value
    return flattened


def build_meta_insights_details(insights: dict[str, Any]) -> list[dict[str, Any]]:
    label_map = {
        "page_impressions_unique": "觸及人數",
        "page_post_engagements": "貼文互動",
        "page_views_total": "頁面瀏覽",
        "page_total_actions": "聯絡動作",
        "post_impressions": "貼文曝光",
        "post_impressions_unique": "貼文觸及",
        "post_engagements": "貼文互動",
        "post_clicks": "貼文點擊",
        "impressions": "IG 曝光",
        "reach": "IG 觸及",
        "saved": "IG 收藏",
    }
    details: list[dict[str, Any]] = []
    for section_key in ("facebook_page_insights", "facebook_post_insights", "instagram_media_insights"):
        section = insights.get(section_key, {})
        if not isinstance(section, dict):
            continue
        for key, value in section.items():
            if key not in label_map:
                continue
            if isinstance(value, (int, float, str)):
                details.append({"label": label_map[key], "value": value})
    return details


def build_meta_insights_fallback_summary(insights: dict[str, Any]) -> str:
    page = insights.get("facebook_page_insights", {}) if isinstance(insights.get("facebook_page_insights", {}), dict) else {}
    post = insights.get("facebook_post_insights", {}) if isinstance(insights.get("facebook_post_insights", {}), dict) else {}
    ig_media = insights.get("instagram_media_insights", {}) if isinstance(insights.get("instagram_media_insights", {}), dict) else {}

    reach = page.get("page_impressions_unique", 0)
    engagements = page.get("page_post_engagements", 0)
    post_clicks = post.get("post_clicks", 0)
    ig_reach = ig_media.get("reach", 0)

    return (
        f"近一週 Facebook 觸及 {reach}、互動 {engagements}；"
        f"近期貼文點擊 {post_clicks}，Instagram 觸及 {ig_reach}。"
        "如果互動高但聯絡動作偏低，下一步建議優先補強 CTA 與預約導流。"
    )


async def summarize_meta_insights(
    *,
    tenant_name: str,
    industry_type: str,
    insights: dict[str, Any],
    consultant: Any | None = None,
) -> dict[str, Any]:
    details = build_meta_insights_details(insights)
    fallback_summary = build_meta_insights_fallback_summary(insights)

    if consultant is None:
        return {"summary": fallback_summary, "details": details}

    prompt = (
        "請根據以下 Meta 成效數據，用繁體中文給老闆一段 90 到 160 字摘要，"
        "先指出最重要的數字，再給一個最務實的下一步建議。\n\n"
        f"數據：{json.dumps(insights, ensure_ascii=False)}"
    )
    try:
        summary = await consultant.build_reply(
            tenant_name=tenant_name,
            industry_type=industry_type,
            message=prompt,
        )
    except Exception:
        summary = ""

    normalized = str(summary or "").strip()
    return {
        "summary": normalized or fallback_summary,
        "details": details,
    }


def build_meta_period_label(period: str) -> str:
    return "近一週" if period in {"week", "7daysAgo"} else "近一月"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_meta_event_timestamp(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        raw = float(value)
        if raw > 10_000_000_000:
            raw /= 1000.0
        return datetime.fromtimestamp(raw, tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    if text.isdigit():
        return _parse_meta_event_timestamp(int(text))
    try:
        return _as_utc(datetime.fromisoformat(text.replace("Z", "+00:00")))
    except ValueError:
        return None


def _resolve_meta_occurred_at(*candidates: Any) -> datetime | None:
    for candidate in candidates:
        parsed = _parse_meta_event_timestamp(candidate)
        if parsed is not None:
            return parsed
    return None


def _parse_meta_page_candidates(raw: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return payload if isinstance(payload, list) else []


def _normalize_meta_page_candidate(payload: dict[str, Any]) -> dict[str, Any]:
    instagram_account = payload.get("instagram_business_account") if isinstance(payload.get("instagram_business_account"), dict) else {}
    return {
        "page_id": str(payload.get("id", "") or "").strip(),
        "page_name": str(payload.get("name", "") or "").strip(),
        "page_access_token": str(payload.get("access_token", "") or "").strip(),
        "ig_user_id": str(instagram_account.get("id", "") or "").strip(),
        "ig_username": str(instagram_account.get("username", "") or "").strip(),
    }


def _append_query(url: str, params: dict[str, str]) -> str:
    filtered = {key: value for key, value in params.items() if str(value or "").strip()}
    if not filtered:
        return url
    parts = urlsplit(url)
    existing = dict(parse_qsl(parts.query, keep_blank_values=True))
    existing.update(filtered)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(existing), parts.fragment))


def build_meta_manage_url(*, settings: Settings, tenant_id: str, line_user_id: str = "") -> str:
    base_url = str(getattr(settings, "KACHU_BASE_URL", "") or "").rstrip("/")
    url = f"{base_url}/tenants/{tenant_id}/meta/manage" if base_url else f"/tenants/{tenant_id}/meta/manage"
    return _append_query(url, {"line_user_id": line_user_id})


def build_meta_connect_launch_url(*, settings: Settings, tenant_id: str, line_user_id: str = "") -> str:
    base_url = str(getattr(settings, "KACHU_BASE_URL", "") or "").rstrip("/")
    url = f"{base_url}/tenants/{tenant_id}/meta/connect/launch" if base_url else f"/tenants/{tenant_id}/meta/connect/launch"
    return _append_query(url, {"line_user_id": line_user_id})


def _build_meta_session_page_url(*, settings: Settings, session_id: str) -> str:
    base_url = str(getattr(settings, "KACHU_BASE_URL", "") or "").rstrip("/")
    return f"{base_url}/meta/connect/{session_id}/page" if base_url else f"/meta/connect/{session_id}/page"


def _render_meta_shell(*, title: str, eyebrow: str, body_html: str) -> str:
    return f"""
<!DOCTYPE html>
<html lang=\"zh-Hant\">
<head>
    <meta charset=\"utf-8\">
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
    <title>{escape(title)} | Kachu+</title>
    <style>
        :root {{
            --bg: #f7f1e8;
            --ink: #13212b;
            --muted: #5f6b73;
            --card: rgba(255,255,255,0.88);
            --line: rgba(19,33,43,0.12);
            --accent: #c94f2d;
            --accent-2: #2a6f97;
            --warn: #b45309;
            --ok: #166534;
            --shadow: 0 18px 50px rgba(19,33,43,0.12);
        }}
        * {{ box-sizing: border-box; }}
        body {{
            margin: 0;
            font-family: "Iowan Old Style", "Palatino Linotype", "Noto Serif TC", serif;
            color: var(--ink);
            background:
                radial-gradient(circle at top right, rgba(201,79,45,0.18), transparent 22%),
                radial-gradient(circle at left bottom, rgba(42,111,151,0.16), transparent 28%),
                linear-gradient(180deg, #f8efe2 0%, var(--bg) 100%);
            min-height: 100vh;
        }}
        .wrap {{ max-width: 860px; margin: 0 auto; padding: 32px 18px 56px; }}
        .card {{ background: var(--card); border: 1px solid var(--line); border-radius: 28px; box-shadow: var(--shadow); overflow: hidden; }}
        .hero {{ padding: 28px 28px 12px; }}
        .eyebrow {{ letter-spacing: 0.08em; text-transform: uppercase; font-size: 12px; color: var(--accent-2); font-family: "Avenir Next", "Noto Sans TC", sans-serif; font-weight: 700; }}
        h1 {{ margin: 10px 0 0; font-size: clamp(30px, 5vw, 44px); line-height: 1.05; }}
        .body {{ padding: 8px 28px 30px; font-family: "Avenir Next", "Noto Sans TC", sans-serif; }}
        .section {{ border-top: 1px solid var(--line); padding: 22px 0; }}
        .section:first-child {{ border-top: 0; }}
        .lead {{ font-size: 16px; line-height: 1.7; color: var(--muted); margin: 10px 0 0; }}
        .grid {{ display: grid; gap: 14px; }}
        .grid.two {{ grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); }}
        .tile {{ border: 1px solid var(--line); border-radius: 20px; padding: 16px; background: rgba(255,255,255,0.76); }}
        .tile strong {{ display: block; font-size: 18px; margin-bottom: 6px; }}
        .tile p {{ margin: 0; color: var(--muted); line-height: 1.6; }}
        .badge {{ display: inline-block; border-radius: 999px; padding: 6px 12px; font-size: 13px; font-weight: 700; }}
        .badge.ok {{ background: rgba(22,101,52,0.12); color: var(--ok); }}
        .badge.warn {{ background: rgba(180,83,9,0.14); color: var(--warn); }}
        .badge.info {{ background: rgba(42,111,151,0.14); color: var(--accent-2); }}
        .actions {{ display: flex; flex-wrap: wrap; gap: 12px; margin-top: 18px; }}
        .button, button {{
            appearance: none; border: 0; border-radius: 999px; padding: 13px 18px; cursor: pointer; text-decoration: none;
            font: 700 15px/1 "Avenir Next", "Noto Sans TC", sans-serif; transition: transform .16s ease, opacity .16s ease;
        }}
        .button:hover, button:hover {{ transform: translateY(-1px); }}
        .button.primary, button.primary {{ background: var(--ink); color: #fff; }}
        .button.secondary, button.secondary {{ background: rgba(19,33,43,0.08); color: var(--ink); }}
        .button.warn, button.warn {{ background: #8a2f1a; color: #fff; }}
        .note {{ border-left: 4px solid var(--accent); padding: 12px 14px; background: rgba(201,79,45,0.08); color: var(--ink); border-radius: 14px; line-height: 1.6; }}
        .list {{ display: grid; gap: 12px; margin-top: 14px; }}
        .small {{ color: var(--muted); font-size: 14px; line-height: 1.6; }}
        form {{ margin: 0; }}
        .inline-form {{ display: inline; }}
    </style>
</head>
<body>
    <main class=\"wrap\">
        <section class=\"card\">
            <div class=\"hero\">
                <div class=\"eyebrow\">{escape(eyebrow)}</div>
                <h1>{escape(title)}</h1>
            </div>
            <div class=\"body\">{body_html}</div>
        </section>
    </main>
</body>
</html>
"""


def _render_manage_page(*, tenant_name: str, tenant_id: str, status_payload: dict[str, Any], settings: Settings, line_user_id: str = "", flash: str = "") -> HTMLResponse:
        connector = status_payload.get("connector") or {}
        connected = bool(status_payload.get("connected"))
        active_sessions = status_payload.get("active_sessions") or []
        ig_user_id = str(connector.get("ig_user_id", "") or "").strip()
        flash_html = ""
        if flash == "disconnected":
                flash_html = '<div class="section"><div class="note">已解除 Meta 連接。若要重新接通，可直接重新授權。</div></div>'
        elif flash == "connected":
                flash_html = '<div class="section"><div class="note">Meta 已完成連接，你現在可以直接請 Kachu+ 幫你查 Facebook 成效。</div></div>'
        elif flash:
            flash_html = f'<div class="section"><div class="note">{escape(flash)}</div></div>'

        launch_url = build_meta_connect_launch_url(settings=settings, tenant_id=tenant_id, line_user_id=line_user_id)
        disconnect_action = _append_query(f"/tenants/{tenant_id}/meta/disconnect-web", {"line_user_id": line_user_id})
        session_links = "".join(
                f'<div class="tile"><strong>授權流程未完成</strong><p>狀態：{escape(str(item.get("status", "")))}。你可以從上次停下的地方繼續。</p><div class="actions"><a class="button secondary" href="{escape(_build_meta_session_page_url(settings=settings, session_id=str(item.get("session_id", ""))))}">回到授權流程</a></div></div>'
                for item in active_sessions
                if item.get("session_id")
        )

        if connected:
                current_block = f"""
                <div class="tile">
                    <strong>{escape(str(connector.get("account_label", "Meta")) or "Meta")}</strong>
                    <p>Facebook 粉專 ID：{escape(str(connector.get("fb_page_id", "") or "未記錄"))}</p>
                    <p>Instagram：{'已連接' if ig_user_id else '尚未連接'}</p>
                    <div class="actions">
                        <a class="button primary" href="{escape(launch_url)}">重新授權</a>
                        <a class="button warn" href="{escape(disconnect_action)}">解除綁定</a>
                    </div>
                </div>
                """
        else:
                current_block = f"""
                <div class="tile">
                    <strong>目前尚未連接 Meta</strong>
                    <p>完成授權後，你就可以請 Kachu+ 幫你整理 Facebook 成效、之後也能接上 FB/IG 發文流程。</p>
                    <div class="actions">
                        <a class="button primary" href="{escape(launch_url)}">立即開始連接</a>
                    </div>
                </div>
                """

        active_section = ""
        if session_links:
                active_section = f'<div class="section"><div class="grid">{session_links}</div></div>'

        body_html = f"""
        <div class="section">
            <div class="badge {'ok' if connected else 'info'}">{'已連接' if connected else '尚未連接'}</div>
            <p class="lead">{escape(tenant_name or '這間店')} 的 Meta 管理中心。這裡可以開始授權、重新授權、解除綁定，並確認目前連到哪個 Facebook 粉專。</p>
        </div>
        {flash_html}
        <div class="section">
            <div class="grid two">
                {current_block}
                <div class="tile">
                    <strong>這一輪會完成什麼</strong>
                    <p>1. 連接 Facebook 粉專</p>
                    <p>2. 若有綁 Instagram 商業帳號，一起完成 IG 連接</p>
                    <p>3. 連完後同步回到 LINE 通知你結果</p>
                </div>
            </div>
        </div>
        {active_section}
        """
        return HTMLResponse(_render_meta_shell(title="Meta 連接管理", eyebrow="Kachu+ Meta", body_html=body_html))


def _render_session_page(*, tenant_name: str, session_payload: dict[str, Any], settings: Settings) -> HTMLResponse:
        pages = session_payload.get("pages") or []
        session_id = str(session_payload.get("session_id", "") or "")
        status_value = str(session_payload.get("status", "") or "")
        selected_page_name = str(session_payload.get("selected_page_name", "") or "")
        selected_page_id = str(session_payload.get("selected_page_id", "") or "")
        manage_url = build_meta_manage_url(settings=settings, tenant_id=str(session_payload.get("tenant_id", "") or ""))

        if status_value == "completed":
                return _render_success_page(tenant_name=tenant_name, session_payload=session_payload, settings=settings)

        if status_value in {"failed", "expired"}:
                return _render_error_page(
                        title="Meta 連接未完成",
                        message=str(session_payload.get("error_message", "") or "Meta 授權流程未完成，請重新開始。"),
                        action_url=manage_url,
                        action_label="回到管理頁",
                )

        if status_value == "awaiting_overwrite_confirmation":
                confirm_url = _append_query(
                        f"/meta/connect/{session_id}/select-page-web",
                        {"page_id": selected_page_id, "overwrite_existing": "true"},
                )
                body_html = f"""
                <div class="section">
                    <div class="badge warn">需要覆蓋確認</div>
                    <p class="lead">你選擇的粉專是 {escape(selected_page_name or selected_page_id)}。這個商家目前已綁定另一個 Facebook 粉專，若繼續會直接覆蓋舊連接。</p>
                </div>
                <div class="section">
                    <div class="actions">
                        <a class="button primary" href="{escape(confirm_url)}">確認覆蓋並連接</a>
                        <a class="button secondary" href="{escape(manage_url)}">取消並回管理頁</a>
                    </div>
                </div>
                """
                return HTMLResponse(_render_meta_shell(title="確認覆蓋既有連接", eyebrow=f"{tenant_name} / Meta", body_html=body_html))

        page_tiles = []
        for item in pages:
                select_url = _append_query(
                        f"/meta/connect/{session_id}/select-page-web",
                        {"page_id": str(item.get('page_id', '') or '')},
                )
                page_tiles.append(
                        f"""
                        <div class="tile">
                            <strong>{escape(str(item.get('page_name', '') or '未命名粉專'))}</strong>
                            <p>Facebook Page ID：{escape(str(item.get('page_id', '') or ''))}</p>
                            <p>Instagram：{'已連接' if str(item.get('ig_user_id', '') or '').strip() else '尚未連接'}</p>
                            <div class="actions">
                                <a class="button primary" href="{escape(select_url)}">連接這個粉專</a>
                            </div>
                        </div>
                        """
                )
        body_html = f"""
        <div class="section">
            <div class="badge info">選擇 Facebook 粉專</div>
            <p class="lead">為 {escape(tenant_name or '這間店')} 選擇一個要連接的 Facebook 粉專。若該粉專已綁 Instagram 商業帳號，系統會一起帶入。</p>
        </div>
        <div class="section">
            <div class="grid">{''.join(page_tiles)}</div>
        </div>
        <div class="section"><a class="button secondary" href="{escape(manage_url)}">回到管理頁</a></div>
        """
        return HTMLResponse(_render_meta_shell(title="選擇要連接的粉專", eyebrow=f"{tenant_name} / Meta", body_html=body_html))


def _render_success_page(*, tenant_name: str, session_payload: dict[str, Any], settings: Settings) -> HTMLResponse:
        manage_url = _append_query(build_meta_manage_url(settings=settings, tenant_id=str(session_payload.get("tenant_id", "") or "")), {"flash": "connected"})
        page_name = str(session_payload.get("selected_page_name", "") or "未命名粉專")
        ig_connected = bool(str(session_payload.get("selected_ig_user_id", "") or "").strip())
        body_html = f"""
        <div class="section">
            <div class="badge ok">授權完成</div>
            <p class="lead">已成功連接 Facebook 粉專 {escape(page_name)}。{'Instagram 也已一起連接完成。' if ig_connected else '目前尚未偵測到對應的 Instagram 商業帳號，所以這次先完成 Facebook 連接。'}</p>
        </div>
        <div class="section">
            <div class="grid two">
                <div class="tile"><strong>現在可以做的事</strong><p>你可以直接回到 LINE 說「幫我看 Facebook 成效」。</p></div>
                <div class="tile"><strong>後續管理</strong><p>如果之後要換粉專、補接 Instagram 或重新授權，都可以回管理頁處理。</p></div>
            </div>
            <div class="actions"><a class="button primary" href="{escape(manage_url)}">回到管理頁</a></div>
        </div>
        """
        return HTMLResponse(_render_meta_shell(title=f"{tenant_name} 已完成 Meta 連接", eyebrow="Kachu+ Meta", body_html=body_html))


def _render_error_page(*, title: str, message: str, action_url: str, action_label: str) -> HTMLResponse:
        body_html = f"""
        <div class="section">
            <div class="badge warn">流程未完成</div>
            <p class="lead">{escape(message)}</p>
        </div>
        <div class="section"><div class="actions"><a class="button primary" href="{escape(action_url)}">{escape(action_label)}</a></div></div>
        """
        return HTMLResponse(_render_meta_shell(title=title, eyebrow="Kachu+ Meta", body_html=body_html), status_code=409)


async def deliver_meta_connection_result(
    *,
    repo: Any,
    settings: Settings,
    tenant_id: str,
    line_user_id: str,
    page_name: str,
    instagram_connected: bool,
) -> dict[str, Any]:
    access_token = resolve_line_push_access_token(repo=repo, settings=settings, tenant_id=tenant_id)
    if not access_token or not line_user_id:
        return {"status": "skipped", "reason": "line_push_unavailable"}
    message = (
        f"Meta 已連接完成：Facebook 粉專「{page_name or '未命名粉專'}」已接通。"
        + ("Instagram 也已一起接通。" if instagram_connected else "目前尚未接到 Instagram，之後補綁後可再重新授權一次。")
        + " 你現在可以直接說：幫我看 Facebook 成效。"
    )
    try:
            await push_line_messages(to=line_user_id, messages=[text_message(message)], access_token=access_token)
    except httpx.HTTPError:
        logger.exception("tenant=%s meta connect result push failed", tenant_id)
        return {"status": "skipped", "reason": "line_push_failed"}
    return {"status": "sent"}


async def deliver_meta_disconnect_result(*, repo: Any, settings: Settings, tenant_id: str, line_user_id: str) -> dict[str, Any]:
    access_token = resolve_line_push_access_token(repo=repo, settings=settings, tenant_id=tenant_id)
    if not access_token or not line_user_id:
        return {"status": "skipped", "reason": "line_push_unavailable"}
    try:
        await push_line_messages(
            to=line_user_id,
            messages=[text_message("Meta 已解除連接。之後若要重新接通，回到管理頁或在 LINE 說「我要重新授權 FB/IG」即可。")],
            access_token=access_token,
        )
    except httpx.HTTPError:
        logger.exception("tenant=%s meta disconnect result push failed", tenant_id)
        return {"status": "skipped", "reason": "line_push_failed"}
    return {"status": "sent"}


def resolve_line_push_access_token(*, repo: Any, settings: Settings, tenant_id: str) -> str:
    config = repo.get_line_channel_config(tenant_id)
    if config is not None:
        token = str(getattr(config, "channel_access_token", "") or "").strip()
        if token:
            return token
    return str(getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or "").strip()


async def deliver_meta_insights_report(
    *,
    repo: Any,
    settings: Settings,
    tenant_id: str,
    summary: str,
    details: list[dict[str, Any]],
    period: str = "week",
    recipient_line_ids: list[str] | None = None,
) -> dict[str, Any]:
    access_token = resolve_line_push_access_token(repo=repo, settings=settings, tenant_id=tenant_id)
    if not access_token:
        return {"status": "skipped", "reason": "line_channel_access_token_missing", "recipient_count": 0}

    recipients = [str(value).strip() for value in (recipient_line_ids or []) if str(value).strip()]
    if not recipients:
        recipients = resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id=tenant_id)
    if not recipients:
        return {"status": "skipped", "reason": "no_recipients", "recipient_count": 0}

    message = meta_insights_report_message(
        period_label=build_meta_period_label(period),
        summary=summary,
        details=details,
    )
    for recipient_line_id in recipients:
        await push_line_messages(
            to=recipient_line_id,
            messages=[message],
            access_token=access_token,
        )
    return {"status": "sent", "reason": "", "recipient_count": len(recipients)}


class MetaInsightsService:
    def __init__(
        self,
        repo: Any,
        settings: Settings,
        client_factory: Callable[..., MetaGraphClient] | None = None,
    ) -> None:
        self._repo = repo
        self._settings = settings
        self._client_factory = client_factory or (lambda **kwargs: MetaGraphClient(**kwargs))

    def upsert_connector(
        self,
        *,
        tenant_id: str,
        account_label: str,
        access_token: str,
        fb_page_id: str = "",
        ig_user_id: str = "",
        fb_access_token: str = "",
        expires_at: int | None = None,
        recent_fb_post_id: str = "",
        recent_ig_media_id: str = "",
    ) -> Any:
        credentials = {
            "access_token": access_token,
            "fb_page_id": fb_page_id,
            "ig_user_id": ig_user_id,
            "fb_access_token": fb_access_token,
            "expires_at": expires_at,
            "recent_fb_post_id": recent_fb_post_id,
            "recent_ig_media_id": recent_ig_media_id,
        }
        return self._repo.save_connector_account(
            tenant_id=tenant_id,
            platform="meta",
            account_label=account_label,
            credentials_json=json.dumps(credentials, ensure_ascii=False),
        )

    def fetch_insights(
        self,
        *,
        tenant_id: str,
        period: str = "week",
        fb_post_id: str = "",
        ig_media_id: str = "",
    ) -> dict[str, Any]:
        client, credentials = resolve_meta_graph_client(
            repo=self._repo,
            tenant_id=tenant_id,
            client_factory=self._client_factory,
        )

        normalized_period = "week" if period in {"week", "7daysAgo"} else "month"
        page_insights = flatten_insights_payload(client.get_fb_page_insights(period=normalized_period))

        resolved_fb_post_id = fb_post_id.strip() or str(credentials.get("recent_fb_post_id", "") or "").strip()
        resolved_ig_media_id = ig_media_id.strip() or str(credentials.get("recent_ig_media_id", "") or "").strip()

        fb_post_insights: dict[str, Any] = {}
        if resolved_fb_post_id:
            fb_post_insights = flatten_insights_payload(client.get_fb_post_insights(post_id=resolved_fb_post_id))

        ig_media_insights: dict[str, Any] = {}
        if resolved_ig_media_id:
            ig_media_insights = flatten_insights_payload(client.get_ig_media_insights(media_id=resolved_ig_media_id))

        return {
            "status": "ok",
            "period": normalized_period,
            "facebook_page_insights": page_insights,
            "facebook_post_insights": fb_post_insights,
            "instagram_media_insights": ig_media_insights,
            "fb_page_id": str(credentials.get("fb_page_id", "") or ""),
            "ig_user_id": str(credentials.get("ig_user_id", "") or ""),
        }


class MetaOAuthFlowService:
    def __init__(self, repo: Any, settings: Settings) -> None:
        self._repo = repo
        self._settings = settings

    def start_connection(self, *, tenant_id: str, line_user_id: str = "") -> dict[str, Any]:
        self._ensure_oauth_configured()
        tenant = self._repo.get_tenant(tenant_id)
        if tenant is None:
            raise MetaConnectorError("tenant not found")

        state = secrets.token_urlsafe(24)
        expires_at = _utcnow() + timedelta(minutes=15)
        oauth_session = self._repo.create_meta_oauth_session(
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            state=state,
            expires_at=expires_at,
        )
        existing = self._repo.get_connector_account(tenant_id, "meta")
        existing_credentials = load_connector_credentials(existing) if existing is not None else {}
        return {
            "session_id": oauth_session.id,
            "authorize_url": self._build_authorize_url(state=state),
            "expires_at": expires_at.isoformat(),
            "has_existing_connector": existing is not None,
            "existing_fb_page_id": str(existing_credentials.get("fb_page_id", "") or "").strip(),
        }

    def handle_callback(self, *, state: str, code: str = "", error: str = "") -> dict[str, Any]:
        oauth_session = self._repo.get_meta_oauth_session_by_state(state)
        if oauth_session is None:
            raise MetaConnectorError("meta oauth session not found")
        expires_at = _as_utc(oauth_session.expires_at)
        if expires_at is not None and expires_at <= _utcnow():
            self._repo.update_meta_oauth_session(
                session_id=oauth_session.id,
                status="expired",
                error_code="session_expired",
                error_message="Meta 授權流程已過期，請重新開始。",
            )
            raise MetaConnectorError("meta oauth session expired")
        if error:
            self._repo.update_meta_oauth_session(
                session_id=oauth_session.id,
                status="failed",
                error_code="oauth_denied",
                error_message=error,
            )
            raise MetaConnectorError("meta oauth denied")
        if not code.strip():
            raise MetaConnectorError("meta oauth code is required")

        access_token = self._exchange_code_for_user_token(code=code)
        pages = self._fetch_page_candidates(access_token=access_token)
        if not pages:
            self._repo.update_meta_oauth_session(
                session_id=oauth_session.id,
                status="failed",
                user_access_token=access_token,
                error_code="no_pages_available",
                error_message="目前登入的 Meta 帳號沒有可連接的 Facebook 粉專。",
            )
            raise MetaConnectorError("meta oauth returned no manageable pages")

        updated = self._repo.update_meta_oauth_session(
            session_id=oauth_session.id,
            status="selecting_page",
            page_candidates=pages,
            user_access_token=access_token,
        )
        return self.describe_session(updated or oauth_session)

    def describe_session(self, oauth_session: Any) -> dict[str, Any]:
        if oauth_session is None:
            raise MetaConnectorError("meta oauth session not found")
        existing = self._repo.get_connector_account(oauth_session.tenant_id, "meta")
        existing_credentials = load_connector_credentials(existing) if existing is not None else {}
        pages = _parse_meta_page_candidates(getattr(oauth_session, "page_candidates_json", "[]"))
        return {
            "session_id": oauth_session.id,
            "tenant_id": oauth_session.tenant_id,
            "line_user_id": oauth_session.line_user_id,
            "status": oauth_session.status,
            "selected_page_id": oauth_session.selected_page_id,
            "selected_page_name": oauth_session.selected_page_name,
            "selected_ig_user_id": oauth_session.selected_ig_user_id,
            "error_code": oauth_session.error_code,
            "error_message": oauth_session.error_message,
            "expires_at": oauth_session.expires_at.isoformat() if oauth_session.expires_at else None,
            "pages": pages,
            "has_existing_connector": existing is not None,
            "existing_fb_page_id": str(existing_credentials.get("fb_page_id", "") or "").strip(),
        }

    def select_page(self, *, session_id: str, page_id: str, overwrite_existing: bool = False) -> dict[str, Any]:
        oauth_session = self._repo.get_meta_oauth_session(session_id)
        if oauth_session is None:
            raise MetaConnectorError("meta oauth session not found")
        pages = _parse_meta_page_candidates(oauth_session.page_candidates_json)
        page = next((item for item in pages if str(item.get("page_id", "") or "").strip() == page_id.strip()), None)
        if page is None:
            raise MetaConnectorError("selected facebook page not found in oauth session")

        existing = self._repo.get_connector_account(oauth_session.tenant_id, "meta")
        existing_credentials = load_connector_credentials(existing) if existing is not None else {}
        existing_fb_page_id = str(existing_credentials.get("fb_page_id", "") or "").strip()
        requires_overwrite = bool(existing is not None and existing_fb_page_id and existing_fb_page_id != page_id.strip())

        if requires_overwrite and not overwrite_existing:
            updated = self._repo.update_meta_oauth_session(
                session_id=session_id,
                status="awaiting_overwrite_confirmation",
                selected_page_id=page.get("page_id", ""),
                selected_page_name=page.get("page_name", ""),
                selected_ig_user_id=page.get("ig_user_id", ""),
                error_code="overwrite_required",
                error_message="此商家目前已綁定另一個 Facebook 粉專，請先確認是否覆蓋。",
            )
            return {
                "status": "overwrite_required",
                "session": self.describe_session(updated or oauth_session),
            }

        connector = MetaInsightsService(self._repo, self._settings).upsert_connector(
            tenant_id=oauth_session.tenant_id,
            account_label=str(page.get("page_name", "") or "Meta"),
            access_token=str(oauth_session.user_access_token or ""),
            fb_page_id=str(page.get("page_id", "") or ""),
            ig_user_id=str(page.get("ig_user_id", "") or ""),
            fb_access_token=str(page.get("page_access_token", "") or oauth_session.user_access_token or ""),
        )
        updated = self._repo.update_meta_oauth_session(
            session_id=session_id,
            status="completed",
            selected_page_id=page.get("page_id", ""),
            selected_page_name=page.get("page_name", ""),
            selected_ig_user_id=page.get("ig_user_id", ""),
            fb_page_access_token=page.get("page_access_token", ""),
            error_code="",
            error_message="",
            completed_at=_utcnow(),
        )
        return {
            "status": "connected",
            "session": self.describe_session(updated or oauth_session),
            "connector": {
                "tenant_id": connector.tenant_id,
                "platform": connector.platform,
                "account_label": connector.account_label,
                "fb_page_id": str(page.get("page_id", "") or ""),
                "ig_user_id": str(page.get("ig_user_id", "") or ""),
            },
            "instagram_connected": bool(str(page.get("ig_user_id", "") or "").strip()),
        }

    def get_connection_status(self, *, tenant_id: str) -> dict[str, Any]:
        connector = self._repo.get_connector_account(tenant_id, "meta")
        credentials = load_connector_credentials(connector) if connector is not None else {}
        sessions = [self.describe_session(item) for item in self._repo.list_active_meta_oauth_sessions(tenant_id)]
        return {
            "connected": connector is not None,
            "connector": None if connector is None else {
                "tenant_id": connector.tenant_id,
                "platform": connector.platform,
                "account_label": connector.account_label,
                "fb_page_id": str(credentials.get("fb_page_id", "") or "").strip(),
                "ig_user_id": str(credentials.get("ig_user_id", "") or "").strip(),
                "last_refreshed_at": connector.last_refreshed_at.isoformat() if connector.last_refreshed_at else None,
            },
            "active_sessions": sessions,
        }

    def disconnect(self, *, tenant_id: str) -> dict[str, Any]:
        account = self._repo.deactivate_connector_account(tenant_id, "meta")
        if account is None:
            return {"status": "skipped", "reason": "meta_connector_not_found"}
        return {"status": "disconnected", "tenant_id": tenant_id, "platform": "meta"}

    def _ensure_oauth_configured(self) -> None:
        if not self._settings.META_APP_ID or not self._settings.META_APP_SECRET or not self._settings.META_OAUTH_REDIRECT_URI:
            raise MetaConnectorError("meta oauth is not configured")

    def _build_authorize_url(self, *, state: str) -> str:
        query = urlencode(
            {
                "client_id": self._settings.META_APP_ID,
                "redirect_uri": self._settings.META_OAUTH_REDIRECT_URI,
                "scope": self._settings.META_OAUTH_SCOPES,
                "response_type": "code",
                "state": state,
            }
        )
        return f"{META_OAUTH_BASE}?{query}"

    def _exchange_code_for_user_token(self, *, code: str) -> str:
        response = httpx.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": self._settings.META_APP_ID,
                "client_secret": self._settings.META_APP_SECRET,
                "redirect_uri": self._settings.META_OAUTH_REDIRECT_URI,
                "code": code,
            },
            timeout=20.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MetaConnectorError("meta oauth token exchange failed") from exc
        payload = response.json()
        access_token = str(payload.get("access_token", "") or "").strip()
        if not access_token:
            raise MetaConnectorError("meta oauth access token missing")
        return access_token

    def _fetch_page_candidates(self, *, access_token: str) -> list[dict[str, Any]]:
        response = httpx.get(
            f"{GRAPH_BASE}/me/accounts",
            params={
                "fields": "id,name,access_token,instagram_business_account{id,username}",
                "access_token": access_token,
            },
            timeout=20.0,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MetaConnectorError("meta oauth fetch pages failed") from exc
        payload = response.json()
        pages = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(pages, list):
            return []
        return [_normalize_meta_page_candidate(item) for item in pages if isinstance(item, dict)]


def _meta_service(request: Request) -> MetaInsightsService:
    service = getattr(request.app.state, "meta_insights_service", None)
    if service is None:
        service = MetaInsightsService(request.app.state.repository, request.app.state.settings)
        request.app.state.meta_insights_service = service
    return service


def _meta_oauth_service(request: Request) -> MetaOAuthFlowService:
    service = getattr(request.app.state, "meta_oauth_flow_service", None)
    if service is None:
        service = MetaOAuthFlowService(request.app.state.repository, request.app.state.settings)
        request.app.state.meta_oauth_flow_service = service
    return service


async def _draft_meta_reply(
    *,
    consultant: Any,
    tenant_name: str,
    source_label: str,
    author_name: str,
    incoming_text: str,
) -> str:
    reply = await consultant.build_reply(
        tenant_name=tenant_name,
        industry_type="meta_reply",
        message=(
            f"請替 {tenant_name or '這間店'} 撰寫一段 {source_label} 回覆。"
            f"對方：{author_name or '顧客'}。內容：{incoming_text}。"
            "請用繁體中文，保持專業親切，80 字內，只輸出最終回覆。"
        ),
    )
    return str(reply or "").strip()


async def _queue_meta_engagement(
    *,
    request: Request,
    tenant_id: str,
    platform: str,
    engagement_type: str,
    external_thread_id: str,
    external_message_id: str,
    author_name: str,
    author_id: str,
    content_text: str,
    source_payload: dict[str, Any],
    source_label: str,
) -> dict[str, Any]:
    repo = request.app.state.repository
    existing = repo.get_external_engagement_by_message_id(external_message_id)
    if existing is not None:
        return {"status": "duplicate", "engagement_id": existing.id}
    tenant = repo.get_tenant(tenant_id)
    reply_draft = await _draft_meta_reply(
        consultant=request.app.state.consultant,
        tenant_name=getattr(tenant, "name", "") or "你的店",
        source_label=source_label,
        author_name=author_name,
        incoming_text=content_text,
    )
    run_id = f"meta-engagement:{external_message_id}"
    engagement = repo.create_external_engagement(
        tenant_id=tenant_id,
        platform=platform,
        engagement_type=engagement_type,
        external_thread_id=external_thread_id,
        external_message_id=external_message_id,
        author_name=author_name,
        author_id=author_id,
        content_text=content_text,
        source_payload=source_payload,
        status="awaiting_approval",
        reply_draft=reply_draft,
        related_run_id=run_id,
    )
    drafts = {
        "engagement_id": engagement.id,
        "source_label": source_label,
        "author_name": author_name,
        "author_id": author_id,
        "incoming_text": content_text,
        "reply_draft": reply_draft,
        "engagement_type": engagement_type,
    }
    repo.save_pending_approval(
        tenant_id=tenant_id,
        agentos_task_id=engagement.id,
        agentos_run_id=run_id,
        workflow_type="kachu_meta_reply",
        draft_content=json.dumps(drafts, ensure_ascii=False),
    )
    await _notify_meta_pending_approval(
        request=request,
        tenant_id=tenant_id,
        run_id=run_id,
        source_label=source_label,
        author_name=author_name,
        content_text=content_text,
        reply_draft=reply_draft,
    )
    return {"status": "queued", "engagement_id": engagement.id, "run_id": run_id}


async def _notify_meta_pending_approval(
    *,
    request: Request,
    tenant_id: str,
    run_id: str,
    source_label: str,
    author_name: str,
    content_text: str,
    reply_draft: str,
) -> dict[str, Any]:
    repo = request.app.state.repository
    recipients = resolve_tenant_line_recipients(repo=repo, settings=request.app.state.settings, tenant_id=tenant_id)
    access_token = resolve_line_push_access_token(repo=repo, settings=request.app.state.settings, tenant_id=tenant_id)
    if not recipients or not access_token:
        return {"status": "skipped", "reason": "line_push_not_available", "recipient_count": len(recipients)}

    flex = build_external_reply_flex(
        run_id=run_id,
        tenant_id=tenant_id,
        source_label=source_label,
        customer_name=author_name,
        incoming_text=content_text,
        reply_draft=reply_draft,
    )
    for recipient in recipients:
        await push_line_messages(
            to=recipient,
            messages=[
                text_message(f"收到新的{source_label}，我先幫你起草回覆了。"),
                {"type": "flex", "altText": f"{source_label} 回覆草稿", "contents": flex},
            ],
            access_token=access_token,
        )
    return {"status": "sent", "recipient_count": len(recipients)}


async def replay_stored_meta_webhook_event(*, request: Request, event: Any) -> dict[str, Any]:
    if str(getattr(event, "provider", "") or "") != "meta":
        raise ValueError("only meta webhook events are supported")

    try:
        payload = json.loads(getattr(event, "raw_payload_json", "") or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("stored webhook payload is invalid") from exc

    repo = request.app.state.repository
    tenant_id = str(getattr(event, "tenant_id", "") or "").strip()
    event_type = str(getattr(event, "event_type", "") or "").strip()
    source_label = "Facebook 留言" if event_type == "comment" else "Facebook 私訊"

    if event_type == "comment":
        external_message_id = str(getattr(event, "external_event_id", "") or payload.get("comment_id") or payload.get("id") or "").strip()
        author = payload.get("from", {}) if isinstance(payload.get("from", {}), dict) else {}
        author_name = str(author.get("name", "") or "顧客")
        author_id = str(getattr(event, "external_user_id", "") or author.get("id", "") or "").strip()
        content_text = str(payload.get("message", "") or "").strip()
        external_thread_id = str(getattr(event, "external_thread_id", "") or payload.get("post_id", "") or external_message_id).strip()
    elif event_type == "message":
        message = payload.get("message", {}) if isinstance(payload.get("message", {}), dict) else {}
        sender = payload.get("sender", {}) if isinstance(payload.get("sender", {}), dict) else {}
        external_message_id = str(getattr(event, "external_event_id", "") or message.get("mid", "") or "").strip()
        author_name = str(sender.get("name", "") or "顧客")
        author_id = str(getattr(event, "external_user_id", "") or sender.get("id", "") or "").strip()
        content_text = str(message.get("text", "") or "").strip()
        external_thread_id = str(getattr(event, "external_thread_id", "") or external_message_id).strip()
    else:
        raise ValueError(f"unsupported meta event type: {event_type}")

    if not external_message_id or not content_text:
        raise ValueError("stored webhook event is missing message identity or content")

    existing = repo.get_external_engagement_by_message_id(external_message_id)
    if existing is not None:
        run_id = str(existing.related_run_id or f"meta-engagement:{external_message_id}")
        if not str(existing.related_run_id or "").strip():
            repo.update_external_engagement(engagement_id=existing.id, related_run_id=run_id)
        pending = repo.get_pending_approval_by_run_id(run_id)
        if pending is None:
            drafts = {
                "engagement_id": existing.id,
                "source_label": source_label,
                "author_name": existing.author_name or author_name,
                "author_id": existing.author_id or author_id,
                "incoming_text": existing.content_text or content_text,
                "reply_draft": existing.reply_draft,
                "engagement_type": existing.engagement_type,
            }
            repo.save_pending_approval(
                tenant_id=tenant_id,
                agentos_task_id=existing.id,
                agentos_run_id=run_id,
                workflow_type="kachu_meta_reply",
                draft_content=json.dumps(drafts, ensure_ascii=False),
            )
            notification = await _notify_meta_pending_approval(
                request=request,
                tenant_id=tenant_id,
                run_id=run_id,
                source_label=source_label,
                author_name=drafts["author_name"],
                content_text=drafts["incoming_text"],
                reply_draft=drafts["reply_draft"],
            )
            return {
                "status": "requeued_pending_approval",
                "engagement_id": existing.id,
                "run_id": run_id,
                "notification": notification,
            }
        return {"status": "duplicate", "engagement_id": existing.id, "run_id": run_id}

    queued = await _queue_meta_engagement(
        request=request,
        tenant_id=tenant_id,
        platform="meta",
        engagement_type=event_type,
        external_thread_id=external_thread_id,
        external_message_id=external_message_id,
        author_name=author_name,
        author_id=author_id,
        content_text=content_text,
        source_payload=payload,
        source_label=source_label,
    )
    return {"status": "queued", **queued}


@router.put("/tenants/{tenant_id}/meta/connector")
def upsert_meta_connector(tenant_id: str, body: MetaConnectorRequest, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    connector = _meta_service(request).upsert_connector(
        tenant_id=tenant_id,
        account_label=body.account_label,
        access_token=body.access_token,
        fb_page_id=body.fb_page_id,
        ig_user_id=body.ig_user_id,
        fb_access_token=body.fb_access_token,
        expires_at=body.expires_at,
        recent_fb_post_id=body.recent_fb_post_id,
        recent_ig_media_id=body.recent_ig_media_id,
    )
    return {
        "connector": {
            "tenant_id": connector.tenant_id,
            "platform": connector.platform,
            "account_label": connector.account_label,
            "fb_page_id": body.fb_page_id,
            "ig_user_id": body.ig_user_id,
        }
    }


@router.get("/tenants/{tenant_id}/meta/insights")
def get_meta_insights(
    tenant_id: str,
    request: Request,
    period: str = Query(default="week"),
    fb_post_id: str = Query(default=""),
    ig_media_id: str = Query(default=""),
) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    try:
        return _meta_service(request).fetch_insights(
            tenant_id=tenant_id,
            period=period,
            fb_post_id=fb_post_id,
            ig_media_id=ig_media_id,
        )
    except MetaConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/tenants/{tenant_id}/meta/connect/start")
def start_meta_connect(tenant_id: str, body: MetaConnectStartRequest, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    try:
        return _meta_oauth_service(request).start_connection(tenant_id=tenant_id, line_user_id=body.line_user_id)
    except MetaConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/meta/webhook", include_in_schema=False)
def verify_meta_webhook(
    request: Request,
    hub_mode: str = Query(default="", alias="hub.mode"),
    hub_verify_token: str = Query(default="", alias="hub.verify_token"),
    hub_challenge: str = Query(default="", alias="hub.challenge"),
) -> PlainTextResponse:
    token = str(request.app.state.settings.META_WEBHOOK_VERIFY_TOKEN or "").strip()
    if hub_mode == "subscribe" and token and hub_verify_token == token:
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="invalid meta webhook verification")


@router.post("/meta/webhook", include_in_schema=False)
async def ingest_meta_webhook(request: Request) -> dict[str, Any]:
    payload = await request.json()
    repo = request.app.state.repository
    processed = 0
    skipped = 0
    for entry in payload.get("entry", []):
        if not isinstance(entry, dict):
            continue
        page_id = str(entry.get("id", "") or "").strip()
        connector = repo.get_meta_connector_by_page_id(page_id)
        if connector is None:
            skipped += 1
            continue
        tenant_id = connector.tenant_id
        for change in entry.get("changes", []):
            if not isinstance(change, dict):
                continue
            if str(change.get("field", "") or "") != "feed":
                continue
            value = change.get("value", {}) if isinstance(change.get("value", {}), dict) else {}
            if str(value.get("item", "") or "") != "comment":
                continue
            comment_id = str(value.get("comment_id") or value.get("id") or "").strip()
            message_text = str(value.get("message", "") or "").strip()
            from_user = value.get("from", {}) if isinstance(value.get("from", {}), dict) else {}
            author_id = str(from_user.get("id", "") or "").strip()
            if not comment_id or not message_text or author_id == page_id:
                skipped += 1
                continue
            dedupe_key = f"meta:{tenant_id}:comment:{comment_id}"
            if not repo.record_webhook_event_if_new(
                tenant_id=tenant_id,
                provider="meta",
                dedupe_key=dedupe_key,
                event_type="comment",
                raw_payload=value,
                external_event_id=comment_id,
                external_user_id=author_id,
                external_thread_id=str(value.get("post_id", "") or comment_id),
                occurred_at=_resolve_meta_occurred_at(
                    value.get("created_time"),
                    value.get("timestamp"),
                    change.get("time"),
                    entry.get("time"),
                ),
            ):
                skipped += 1
                continue
            await _queue_meta_engagement(
                request=request,
                tenant_id=tenant_id,
                platform="meta",
                engagement_type="comment",
                external_thread_id=str(value.get("post_id", "") or comment_id),
                external_message_id=comment_id,
                author_name=str(from_user.get("name", "") or "顧客"),
                author_id=author_id,
                content_text=message_text,
                source_payload=value,
                source_label="Facebook 留言",
            )
            processed += 1
        for messaging in entry.get("messaging", []):
            if not isinstance(messaging, dict):
                continue
            message = messaging.get("message", {}) if isinstance(messaging.get("message", {}), dict) else {}
            message_id = str(message.get("mid", "") or "").strip()
            message_text = str(message.get("text", "") or "").strip()
            sender = messaging.get("sender", {}) if isinstance(messaging.get("sender", {}), dict) else {}
            author_id = str(sender.get("id", "") or "").strip()
            if not message_id or not message_text or author_id == page_id:
                skipped += 1
                continue
            dedupe_key = f"meta:{tenant_id}:message:{message_id}"
            if not repo.record_webhook_event_if_new(
                tenant_id=tenant_id,
                provider="meta",
                dedupe_key=dedupe_key,
                event_type="message",
                raw_payload=messaging,
                external_event_id=message_id,
                external_user_id=author_id,
                external_thread_id=message_id,
                occurred_at=_resolve_meta_occurred_at(
                    message.get("created_time"),
                    message.get("timestamp"),
                    messaging.get("timestamp"),
                    entry.get("time"),
                ),
            ):
                skipped += 1
                continue
            await _queue_meta_engagement(
                request=request,
                tenant_id=tenant_id,
                platform="meta",
                engagement_type="message",
                external_thread_id=message_id,
                external_message_id=message_id,
                author_name=str(sender.get("name", "") or "顧客"),
                author_id=author_id,
                content_text=message_text,
                source_payload=messaging,
                source_label="Facebook 私訊",
            )
            processed += 1
    return {"status": "ok", "processed": processed, "skipped": skipped}


@router.get("/tenants/{tenant_id}/meta/engagements")
def list_meta_engagements(tenant_id: str, request: Request, status_value: str = Query(default=""), limit: int = Query(default=20, le=50)) -> dict[str, Any]:
    repo = request.app.state.repository
    entries = repo.list_pending_external_engagements(tenant_id, statuses=[status_value] if status_value else None, limit=limit)
    return {
        "items": [
            {
                "id": item.id,
                "platform": item.platform,
                "engagement_type": item.engagement_type,
                "author_name": item.author_name,
                "content_text": item.content_text,
                "status": item.status,
                "reply_draft": item.reply_draft,
                "related_run_id": item.related_run_id,
            }
            for item in entries
        ]
    }


@router.get("/tenants/{tenant_id}/meta/manage", response_class=HTMLResponse)
def meta_manage_page(
    tenant_id: str,
    request: Request,
    line_user_id: str = Query(default=""),
    flash: str = Query(default=""),
) -> HTMLResponse:
    repo = request.app.state.repository
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    status_payload = _meta_oauth_service(request).get_connection_status(tenant_id=tenant_id)
    return _render_manage_page(
        tenant_name=str(getattr(tenant, "name", "") or ""),
        tenant_id=tenant_id,
        status_payload=status_payload,
        settings=request.app.state.settings,
        line_user_id=line_user_id,
        flash=flash,
    )


@router.get("/tenants/{tenant_id}/meta/connect/launch")
def launch_meta_connect(
    tenant_id: str,
    request: Request,
    line_user_id: str = Query(default=""),
) -> RedirectResponse:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    try:
        payload = _meta_oauth_service(request).start_connection(tenant_id=tenant_id, line_user_id=line_user_id)
    except MetaConnectorError as exc:
        return RedirectResponse(
            url=_append_query(build_meta_manage_url(settings=request.app.state.settings, tenant_id=tenant_id, line_user_id=line_user_id), {"flash": str(exc)}),
            status_code=status.HTTP_303_SEE_OTHER,
        )
    return RedirectResponse(url=payload["authorize_url"], status_code=status.HTTP_303_SEE_OTHER)


@router.get("/meta/oauth/callback")
def meta_oauth_callback(
    request: Request,
    state: str = Query(default=""),
    code: str = Query(default=""),
    error: str = Query(default=""),
):
    try:
        payload = _meta_oauth_service(request).handle_callback(state=state, code=code, error=error)
    except MetaConnectorError as exc:
        fallback_url = str(getattr(request.app.state.settings, "KACHU_BASE_URL", "") or "").rstrip("/") or "/"
        return _render_error_page(
            title="Meta 授權失敗",
            message=str(exc),
            action_url=fallback_url,
            action_label="回到管理頁重新開始",
        )
    return RedirectResponse(
        url=_build_meta_session_page_url(settings=request.app.state.settings, session_id=payload["session_id"]),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.get("/meta/connect/{session_id}")
def get_meta_connect_session(session_id: str, request: Request) -> dict[str, Any]:
    oauth_session = request.app.state.repository.get_meta_oauth_session(session_id)
    if oauth_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meta oauth session not found")
    return _meta_oauth_service(request).describe_session(oauth_session)


@router.get("/meta/connect/{session_id}/page", response_class=HTMLResponse)
async def get_meta_connect_session_page(session_id: str, request: Request) -> HTMLResponse:
    repo = request.app.state.repository
    oauth_session = repo.get_meta_oauth_session(session_id)
    if oauth_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meta oauth session not found")
    tenant = repo.get_tenant(oauth_session.tenant_id)
    session_payload = _meta_oauth_service(request).describe_session(oauth_session)
    pages = session_payload.get("pages") or []
    if session_payload.get("status") == "selecting_page" and len(pages) == 1:
        result = _meta_oauth_service(request).select_page(session_id=session_id, page_id=str(pages[0].get("page_id", "") or ""))
        if result.get("status") == "connected":
            line_user_id = str((result.get("session") or {}).get("line_user_id", "") or "")
            if line_user_id:
                await deliver_meta_connection_result(
                    repo=repo,
                    settings=request.app.state.settings,
                    tenant_id=oauth_session.tenant_id,
                    line_user_id=line_user_id,
                    page_name=str((result.get("session") or {}).get("selected_page_name", "") or ""),
                    instagram_connected=bool(result.get("instagram_connected")),
                )
            return _render_success_page(
                tenant_name=str(getattr(tenant, "name", "") or ""),
                session_payload=result["session"],
                settings=request.app.state.settings,
            )
        session_payload = result.get("session") or session_payload
    return _render_session_page(
        tenant_name=str(getattr(tenant, "name", "") or ""),
        session_payload=session_payload,
        settings=request.app.state.settings,
    )


@router.post("/meta/connect/{session_id}/select-page")
def select_meta_page(session_id: str, body: MetaPageSelectionRequest, request: Request) -> dict[str, Any]:
    try:
        return _meta_oauth_service(request).select_page(
            session_id=session_id,
            page_id=body.page_id,
            overwrite_existing=body.overwrite_existing,
        )
    except MetaConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.get("/meta/connect/{session_id}/select-page-web", response_class=HTMLResponse)
async def select_meta_page_web(
    session_id: str,
    request: Request,
    page_id: str = Query(default=""),
    overwrite_existing: str = Query(default="false"),
) -> HTMLResponse:
    repo = request.app.state.repository
    oauth_session = repo.get_meta_oauth_session(session_id)
    if oauth_session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="meta oauth session not found")
    tenant = repo.get_tenant(oauth_session.tenant_id)
    try:
        result = _meta_oauth_service(request).select_page(
            session_id=session_id,
            page_id=page_id,
            overwrite_existing=str(overwrite_existing).lower() in {"1", "true", "yes", "on"},
        )
    except MetaConnectorError as exc:
        return _render_error_page(
            title="粉專連接失敗",
            message=str(exc),
            action_url=_build_meta_session_page_url(settings=request.app.state.settings, session_id=session_id),
            action_label="回上一頁重試",
        )
    if result.get("status") == "connected":
        session_payload = result.get("session") or {}
        line_user_id = str(session_payload.get("line_user_id", "") or "")
        if line_user_id:
            await deliver_meta_connection_result(
                repo=repo,
                settings=request.app.state.settings,
                tenant_id=oauth_session.tenant_id,
                line_user_id=line_user_id,
                page_name=str(session_payload.get("selected_page_name", "") or ""),
                instagram_connected=bool(result.get("instagram_connected")),
            )
        return _render_success_page(
            tenant_name=str(getattr(tenant, "name", "") or ""),
            session_payload=session_payload,
            settings=request.app.state.settings,
        )
    return _render_session_page(
        tenant_name=str(getattr(tenant, "name", "") or ""),
        session_payload=result.get("session") or _meta_oauth_service(request).describe_session(oauth_session),
        settings=request.app.state.settings,
    )


@router.get("/tenants/{tenant_id}/meta/status")
def get_meta_connection_status(tenant_id: str, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return _meta_oauth_service(request).get_connection_status(tenant_id=tenant_id)


@router.post("/tenants/{tenant_id}/meta/disconnect")
def disconnect_meta(tenant_id: str, body: MetaDisconnectRequest, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    return _meta_oauth_service(request).disconnect(tenant_id=tenant_id)


@router.get("/tenants/{tenant_id}/meta/disconnect-web")
async def disconnect_meta_web(
    tenant_id: str,
    request: Request,
    line_user_id: str = Query(default=""),
) -> RedirectResponse:
    repo = request.app.state.repository
    tenant = repo.get_tenant(tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="tenant not found")
    _meta_oauth_service(request).disconnect(tenant_id=tenant_id)
    if line_user_id:
        await deliver_meta_disconnect_result(
            repo=repo,
            settings=request.app.state.settings,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
        )
    return RedirectResponse(
        url=_append_query(build_meta_manage_url(settings=request.app.state.settings, tenant_id=tenant_id, line_user_id=line_user_id), {"flash": "disconnected"}),
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/tools/fetch-meta-insights")
def fetch_meta_insights(body: FetchMetaInsightsRequest, request: Request) -> dict[str, Any]:
    try:
        return _meta_service(request).fetch_insights(
            tenant_id=body.tenant_id,
            period=body.period,
            fb_post_id=body.fb_post_id,
            ig_media_id=body.ig_media_id,
        )
    except MetaConnectorError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


@router.post("/tools/generate-meta-insights-summary")
async def generate_meta_insights_summary(body: GenerateMetaInsightsSummaryRequest, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    tenant = repo.get_tenant(body.tenant_id)
    return {
        "status": "ok",
        **await summarize_meta_insights(
            tenant_name=getattr(tenant, "name", ""),
            industry_type=getattr(tenant, "industry_type", ""),
            insights=body.insights,
            consultant=getattr(request.app.state, "consultant", None),
        ),
    }


@router.post("/tools/send-meta-insights-report")
async def send_meta_insights_report(body: SendMetaInsightsReportRequest, request: Request) -> dict[str, Any]:
    repo = request.app.state.repository
    settings = request.app.state.settings
    return await deliver_meta_insights_report(
        repo=repo,
        settings=settings,
        tenant_id=body.tenant_id,
        summary=body.summary,
        details=body.details,
        period=body.period,
        recipient_line_ids=body.recipient_line_ids,
    )