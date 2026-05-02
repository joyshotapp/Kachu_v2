from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import json
import logging
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from .. import document_parser
from ..agentOS_client import AgentOSClient
from ..approval_bridge import ApprovalBridge
from ..business_consultant import BusinessConsultant
from ..conversation_context import COMMAND_CONVERSATION_TYPE, CONSULTATION_CONVERSATION_TYPE
from ..config import Settings
from ..intent_router import IntentRouter
from ..knowledge_capture import KnowledgeCaptureService
from ..memory import MemoryManager
from ..models import ApprovalAction, BossRouteDecision, BossRouteMode, Intent
from ..onboarding import OnboardingFlow
from ..persistence import KachuRepository
from .push import push_line_messages, text_message



logger = logging.getLogger(__name__)



router = APIRouter(prefix="/webhooks", tags=["line"])

_PENDING_BOSS_ASSET_INTENT = "pending_boss_asset_intent"
_PENDING_BOSS_TEXT_INTENT = "pending_boss_text_intent"
_PENDING_SCHEDULE_REQUEST = "pending_schedule_request"
_PENDING_SCHEDULE_CONFIRMATION = "pending_schedule_confirmation"
_EXPLICIT_KNOWLEDGE_CUES = frozenset([
    "品牌資訊", "品牌資料", "產品資訊", "品牌定位", "品牌故事", "這是我們的",
    "幫我記住", "記住這個", "記下來", "吸收這份", "內化這份",
    "作為品牌資料", "補充資料",
])
_PUBLISH_CUES = frozenset([
    "發文", "貼文", "發貼文", "po文", "發布", "拿來發", "幫我發",
    "寫文案", "宣傳貼文", "上架文案",
])
_CONSULT_CUES = frozenset([
    "腦力激盪", "討論", "策略", "定位", "客群", "市場", "溝通", "方向",
    "怎麼想", "怎麼做", "你覺得", "要怎麼", "分析", "理解", "了解", "看法", "建議", "評估",
])

_DOCUMENT_LIKE_CUES: frozenset[str] = frozenset()  # kept for backward compat; no longer used in classification
_QUESTION_CUES = frozenset([
    "?", "？", "嗎", "呢", "怎麼", "如何", "為什麼", "哪個", "哪些", "是不是",
    "要怎麼", "你覺得", "可不可以", "行不行",
])


async def _handle_event_logged(**kwargs: Any) -> None:
    event = kwargs.get("event", {})
    source = event.get("source", {})
    message = event.get("message", {})
    try:
        await _handle_event(**kwargs)
    except Exception:
        logger.exception(
            "Unhandled LINE event error: type=%s user=%s message_type=%s text=%s",
            event.get("type", ""),
            source.get("userId", ""),
            message.get("type", ""),
            str(message.get("text", ""))[:120],
        )





def _verify_line_signature(body: bytes, channel_secret: str, signature: str) -> bool:
    digest = hmac.new(
        channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)





def _parse_postback_data(data: str) -> dict[str, str]:

    parsed = parse_qs(data)

    return {k: v[0] for k, v in parsed.items()}


def _build_photo_preview_url(base_url: str, run_id: str) -> str:

    return f"{base_url.rstrip('/')}/tools/approval-photo/{run_id}"


def _build_small_talk_reply(text: str) -> str:

    normalized = text.strip()
    if "早安" in normalized:
        return "早安，我在。你可以直接跟我說今天想處理什麼。"
    if "午安" in normalized:
        return "午安，我在。今天要我先幫你處理哪件事？"
    if "晚安" in normalized:
        return "晚安，我在。若你要排程、發文或看數據，直接跟我說就行。"
    if "辛苦了" in normalized:
        return "收到，也謝謝你。我在這裡，想處理什麼直接跟我說。"
    if "謝謝" in normalized:
        return "不客氣，我在。接下來想處理什麼，直接跟我說。"
    return "你好，我在。你可以直接跟我說想做的事，例如發文、看流量、更新店家資訊，或傳照片給我。"


def _is_retriable_line_download_error(exc: httpx.HTTPError) -> bool:

    if isinstance(exc, httpx.TimeoutException):

        return True

    if isinstance(exc, httpx.HTTPStatusError):

        status_code = exc.response.status_code

        return status_code == 429 or status_code >= 500

    return isinstance(exc, (httpx.NetworkError, httpx.RemoteProtocolError))


async def _download_line_content_with_retry(

    message_id: str,

    access_token: str,

    *,

    max_attempts: int = 3,

) -> bytes:

    last_error: httpx.HTTPError | None = None

    for attempt in range(1, max_attempts + 1):

        try:

            return await _download_line_image(message_id, access_token)

        except httpx.HTTPError as exc:

            last_error = exc

            if attempt >= max_attempts or not _is_retriable_line_download_error(exc):

                raise

            delay_seconds = min(0.5 * (2 ** (attempt - 1)), 2.0)

            logger.warning(

                "Retrying LINE content download for message_id=%s attempt=%s/%s after error: %s",

                message_id,

                attempt,

                max_attempts,

                exc,

            )

            await asyncio.sleep(delay_seconds)


    if last_error is not None:

        raise last_error

    raise RuntimeError("LINE content download failed without an HTTP error")


async def _notify_processing_failure(

    *,

    line_user_id: str,

    settings: Settings,

    text: str,

) -> None:

    if not line_user_id or not settings.LINE_CHANNEL_ACCESS_TOKEN:

        return

    try:

        await push_line_messages(

            to=line_user_id,

            messages=[text_message(text)],

            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

        )

    except httpx.HTTPError as exc:

        logger.error("Failed to send LINE processing failure notice: %s", exc)


def _contains_any(text: str, keywords: frozenset[str]) -> bool:
    lowered = text.lower()
    return any(keyword.lower() in lowered for keyword in keywords)


def _should_absorb_explicit_knowledge_text(text: str) -> bool:
    if not _contains_any(text, _EXPLICIT_KNOWLEDGE_CUES):
        return False
    if _contains_any(text, _QUESTION_CUES):
        return False
    if _contains_any(text, _CONSULT_CUES):
        return False
    return True


def _clear_pending_asset_context(repo: KachuRepository, tenant_id: str) -> None:
    repo.save_shared_context(
        tenant_id=tenant_id,
        context_type=_PENDING_BOSS_ASSET_INTENT,
        content={"resolved": True},
        ttl_hours=-1,
    )


def _clear_pending_schedule_contexts(repo: KachuRepository, tenant_id: str) -> None:
    for context_type in (_PENDING_SCHEDULE_REQUEST, _PENDING_SCHEDULE_CONFIRMATION):
        repo.save_shared_context(
            tenant_id=tenant_id,
            context_type=context_type,
            content={"resolved": True},
            ttl_hours=-1,
        )


def _derive_schedule_platforms(workflow_type: str, draft_content: dict[str, Any]) -> list[str]:
    selected_platforms = draft_content.get("selected_platforms")
    if isinstance(selected_platforms, list):
        normalized = [str(platform).strip() for platform in selected_platforms if str(platform).strip()]
        if normalized:
            return normalized

    platforms: list[str] = []
    if draft_content.get("ig_fb"):
        platforms.append("ig_fb")
    if draft_content.get("google"):
        platforms.append("google")
    if platforms:
        return platforms
    if workflow_type in ("kachu_google_post", "google_post"):
        return ["google"]
    return ["ig_fb", "google"]


def _schedule_prompt_message() -> dict[str, Any]:
    return text_message(
        "請直接告訴我預計發布時間，例如：5月3日晚上8點 或 5/3 20:30。\n"
        "你必須完整說出幾月、幾日、幾時；如果沒有講幾分，我會當作整點。"
    )


def _format_schedule_time(dt: datetime) -> str:
    weekday_map = "一二三四五六日"
    return f"{dt.month}月{dt.day}日（週{weekday_map[dt.weekday()]}）{dt.hour:02d}:{dt.minute:02d}"


def _build_schedule_confirmation_message(run_id: str, tenant_id: str, scheduled_label: str) -> dict[str, Any]:
    return {
        "type": "text",
        "text": f"我會在 {scheduled_label} 幫你發布。確認無誤後，請點「確認排程」。",
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "postback",
                        "label": "確認排程",
                        "data": f"action=confirm_schedule_publish&run_id={run_id}&tenant_id={tenant_id}",
                        "displayText": "確認排程",
                    },
                },
                {
                    "type": "action",
                    "action": {
                        "type": "postback",
                        "label": "重新輸入",
                        "data": f"action=cancel_schedule_publish&run_id={run_id}&tenant_id={tenant_id}",
                        "displayText": "重新輸入排程時間",
                    },
                },
            ]
        },
    }


def _normalize_schedule_text(text: str) -> str:
    translation = str.maketrans({
        "０": "0",
        "１": "1",
        "２": "2",
        "３": "3",
        "４": "4",
        "５": "5",
        "６": "6",
        "７": "7",
        "８": "8",
        "９": "9",
        "：": ":",
        "／": "/",
        "－": "-",
        "　": " ",
    })
    return str(text or "").translate(translation).strip()


def _tenant_now(repo: KachuRepository, tenant_id: str) -> datetime:
    tenant = repo.get_tenant(tenant_id) or repo.get_or_create_tenant(tenant_id)
    timezone_name = getattr(tenant, "timezone", "Asia/Taipei") or "Asia/Taipei"
    try:
        tzinfo = ZoneInfo(timezone_name)
    except Exception:
        tzinfo = ZoneInfo("Asia/Taipei")
    return datetime.now(tzinfo)


def _parse_schedule_datetime(text: str, *, now_local: datetime) -> tuple[datetime | None, str | None]:
    normalized = _normalize_schedule_text(text)
    date_match = re.search(r"(?P<month>\d{1,2})\s*(?:月|/|-)\s*(?P<day>\d{1,2})\s*(?:日)?", normalized)
    if not date_match:
        return None, "請完整說出幾月幾日幾時，例如 5月3日晚上8點。"

    remainder = normalized[date_match.end():]
    time_match = re.search(
        r"(?P<period>凌晨|早上|上午|中午|下午|晚上|晚間)?\s*(?P<hour>\d{1,2})\s*(?:(?:[:：]\s*(?P<minute_colon>\d{1,2}))|(?:\s*(?:點|時)\s*(?P<minute_text>\d{1,2})?\s*(?:分)?))",
        remainder,
    )
    if not time_match:
        return None, "我還缺少發布時段，請用像 5月3日晚上8點 或 5/3 20:30 這樣的格式。"

    month = int(date_match.group("month"))
    day = int(date_match.group("day"))
    hour = int(time_match.group("hour"))
    minute = int(time_match.group("minute_colon") or time_match.group("minute_text") or 0)
    period = time_match.group("period") or ""

    if not 1 <= month <= 12:
        return None, "月份不對，請再說一次。"
    if not 0 <= hour <= 23:
        return None, "小時要在 0 到 23 之間，請再說一次。"
    if not 0 <= minute <= 59:
        return None, "分鐘要在 0 到 59 之間，請再說一次。"

    if period in ("下午", "晚上", "晚間") and hour < 12:
        hour += 12
    elif period in ("凌晨", "早上", "上午") and hour == 12:
        hour = 0
    elif period == "中午" and 1 <= hour <= 11:
        hour += 12

    try:
        candidate = datetime(now_local.year, month, day, hour, minute, tzinfo=now_local.tzinfo)
    except ValueError:
        return None, "日期看起來不對，請再說一次。"

    if candidate <= now_local:
        if (month, day) < (now_local.month, now_local.day):
            candidate = candidate.replace(year=now_local.year + 1)
        else:
            return None, "這個時間已經過了，請給我一個未來時間。"

    return candidate, None


def _build_asset_clarification_message(summary: str) -> dict[str, Any]:
    question = f"收到了！（{summary}）\n這個你要怎麼用？" if summary else "收到了！這個你要怎麼用？"
    return {
        "type": "text",
        "text": question,
        "quickReply": {
            "items": [
                {
                    "type": "action",
                    "action": {
                        "type": "postback",
                        "label": "📤 生成貼文",
                        "data": "action=asset_intent&decision=photo_content",
                        "displayText": "幫我生成貼文",
                    },
                },
                {
                    "type": "action",
                    "action": {
                        "type": "postback",
                        "label": "📚 存品牌資料",
                        "data": "action=asset_intent&decision=knowledge",
                        "displayText": "存入品牌資料",
                    },
                },
                {
                    "type": "action",
                    "action": {
                        "type": "postback",
                        "label": "💬 先討論",
                        "data": "action=asset_intent&decision=consult",
                        "displayText": "先討論",
                    },
                },
            ]
        },
    }


def _build_text_execution_clarification_message(
    intent: Intent,
    actions: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    action_text = {
        Intent.BUSINESS_PROFILE_UPDATE: "幫我更新 Google 商家營業資訊",
        Intent.KNOWLEDGE_UPDATE: "幫我更新這項資訊",
        Intent.GOOGLE_POST: "幫我產出一篇貼文",
        Intent.GA4_REPORT: "幫我拉一份流量報告",
        Intent.REVIEW_REPLY: "幫我處理這則評論",
    }.get(intent, "幫我接著做")
    quick_items = [
        {
            "type": "action",
            "action": {
                "type": "message",
                "label": "先一起討論",
                "text": "先討論",
            },
        }
    ]
    for action in (actions or [])[:2]:
        quick_items.append(
            {
                "type": "action",
                "action": {
                    "type": "postback",
                    "label": action["label"][:20],
                    "data": (
                        f"action=trigger_workflow"
                        f"&workflow="
                        f"{ {'business_profile_update': 'kachu_business_profile_update', 'google_post': 'kachu_google_post', 'knowledge_update': 'kachu_knowledge_update', 'ga4_report': 'kachu_ga4_report', 'review_reply': 'kachu_review_reply'}.get(action['intent'], '') }"
                        f"&intent={action['intent']}"
                        f"&topic={action.get('topic', '')}"
                    ),
                    "displayText": action["label"],
                },
            }
        )
    return {
        "type": "text",
        "text": (
            "我先把這句話理解成你在描述一個經營問題。\n"
            f"如果你要我直接動手，可以點「{action_text}」；如果你想先拆原因或聊方向，就點「先一起討論」。"
        ),
        "quickReply": {"items": quick_items},
    }


_AFFIRM_TOKENS = frozenset(["對", "好", "是", "確認", "沒錯", "對啊", "好啊", "是啊", "ok", "OK", "yes", "Yes"])
_SCHEDULE_SUPPORTED_WORKFLOWS = frozenset(["kachu_photo_content", "photo_content", "kachu_google_post", "google_post"])


def _resolve_pending_text_intent_reply(text: str) -> str:
    if any(t in text for t in _AFFIRM_TOKENS):
        return "execute"
    if any(token in text for token in ("幫我", "給我", "直接")) and any(
        keyword in text for keyword in ("報告", "流量", "數據", "更新", "修改", "調整")
    ):
        return "execute"
    if "直接" in text or "執行" in text or "處理" in text:
        return "execute"
    if "先討論" in text or _contains_any(text, _CONSULT_CUES):
        return "consult"
    return "unknown"


async def _handle_pending_text_intent_reply(
    *,
    repo: KachuRepository,
    tenant_id: str,
    line_user_id: str,
    line_text: str,
    line_message_id: str,
    intent_router: IntentRouter,
    business_consultant: BusinessConsultant | None,
    settings: Settings,
) -> bool:
    pending = repo.get_shared_context(tenant_id, _PENDING_BOSS_TEXT_INTENT)
    if not pending:
        return False

    decision = _resolve_pending_text_intent_reply(line_text)
    if decision == "unknown":
        # 如果新訊息本身能被路由到某個工作流 intent，清掉 pending 讓它重新走 plan_boss_message
        if intent_router is not None:
            fresh_intent = intent_router.classify_text(line_text)
            if fresh_intent in _WORKFLOW_INTENTS:
                repo.save_shared_context(
                    tenant_id=tenant_id,
                    context_type=_PENDING_BOSS_TEXT_INTENT,
                    content={"resolved": True},
                    ttl_hours=-1,
                )
                return False
        clarify_q = pending.get("clarify_question", "")
        if not clarify_q:
            clarify_q = _build_text_execution_clarification_message(
                Intent(pending.get("intent", Intent.GENERAL_CHAT)),
            ).get("text", "")
        if settings.LINE_CHANNEL_ACCESS_TOKEN and clarify_q:
            await push_line_messages(
                to=line_user_id,
                messages=[text_message(clarify_q)],
                access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
            )
        return True

    repo.save_shared_context(
        tenant_id=tenant_id,
        context_type=_PENDING_BOSS_TEXT_INTENT,
        content={"resolved": True},
        ttl_hours=-1,
    )

    if decision == "execute":
        await intent_router.dispatch(
            intent=Intent(pending.get("intent", Intent.GENERAL_CHAT)),
            tenant_id=tenant_id,
            trigger_source="line",
            trigger_payload={
                "message": pending.get("message", ""),
                "topic": pending.get("topic", ""),
                "line_message_id": line_message_id,
            },
        )
        return True

    if business_consultant is None:
        return True

    reply_msg = await business_consultant.build_reply(
        tenant_id=tenant_id,
        message=pending.get("message", ""),
    )
    repo.save_conversation(
        tenant_id=tenant_id,
        role="ai",
        content=reply_msg.get("text", ""),
        conversation_type=CONSULTATION_CONVERSATION_TYPE,
    )
    if settings.LINE_CHANNEL_ACCESS_TOKEN:
        await push_line_messages(
            to=line_user_id,
            messages=[reply_msg],
            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
        )
    return True


async def _handle_pending_schedule_reply(
    *,
    repo: KachuRepository,
    tenant_id: str,
    line_user_id: str,
    line_text: str,
    settings: Settings,
) -> bool:
    try:
        pending = repo.get_shared_context(tenant_id, _PENDING_SCHEDULE_REQUEST)
    except StopIteration:
        return False
    if not pending:
        return False

    scheduled_for, error_text = _parse_schedule_datetime(line_text, now_local=_tenant_now(repo, tenant_id))
    if scheduled_for is None:
        if settings.LINE_CHANNEL_ACCESS_TOKEN and error_text:
            await push_line_messages(
                to=line_user_id,
                messages=[text_message(error_text), _schedule_prompt_message()],
                access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
            )
        return True

    scheduled_label = _format_schedule_time(scheduled_for)
    repo.save_shared_context(
        tenant_id=tenant_id,
        context_type=_PENDING_SCHEDULE_CONFIRMATION,
        content={
            **pending,
            "scheduled_for": scheduled_for.astimezone(timezone.utc).isoformat(),
            "scheduled_label": scheduled_label,
        },
        ttl_hours=24,
    )
    repo.save_shared_context(
        tenant_id=tenant_id,
        context_type=_PENDING_SCHEDULE_REQUEST,
        content={"resolved": True},
        ttl_hours=-1,
    )

    if settings.LINE_CHANNEL_ACCESS_TOKEN:
        await push_line_messages(
            to=line_user_id,
            messages=[_build_schedule_confirmation_message(pending.get("run_id", ""), tenant_id, scheduled_label)],
            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
        )
    return True


def _resolve_pending_asset_reply(text: str) -> str:
    if _contains_any(text, _CONSULT_CUES) or "先一起討論" in text:
        return "consult"
    if _contains_any(text, _PUBLISH_CUES) or "拿來發文" in text:
        return "photo_content"
    if _contains_any(text, _EXPLICIT_KNOWLEDGE_CUES) or "品牌資料" in text:
        return "knowledge"
    return "unknown"


async def _classify_media_after_onboarding(
    *,
    msg_type: str,
    extracted_text: str,
    settings: Settings,
) -> tuple[str, str, str]:
    """Never auto-classify — always ask the user explicitly."""
    summary = extracted_text.strip().splitlines()[0][:80] if extracted_text.strip() else ""
    return "clarify", summary, "always_ask"


async def _handle_pending_asset_reply(
    *,
    repo: KachuRepository,
    tenant_id: str,
    line_user_id: str,
    line_text: str,
    intent_router: IntentRouter,
    business_consultant: BusinessConsultant | None,
    settings: Settings,
    knowledge_capture: KnowledgeCaptureService,
) -> bool:
    pending = repo.get_shared_context(tenant_id, _PENDING_BOSS_ASSET_INTENT)
    if not pending:
        return False

    decision = _resolve_pending_asset_reply(line_text)
    if decision == "unknown":
        if settings.LINE_CHANNEL_ACCESS_TOKEN:
            await push_line_messages(
                to=line_user_id,
                messages=[_build_asset_clarification_message(pending.get("summary", ""))],
                access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
            )
        return True

    _clear_pending_asset_context(repo, tenant_id)

    if decision == "knowledge":
        messages = await knowledge_capture.capture_knowledge_text(
            tenant_id=tenant_id,
            content=pending.get("knowledge_text", ""),
            source_type=pending.get("source_type", "document"),
            source_id=pending.get("source_id"),
            ack_text="我先把這份內容當成品牌資料吸收了。",
        )
        if settings.LINE_CHANNEL_ACCESS_TOKEN:
            await push_line_messages(
                to=line_user_id,
                messages=messages,
                access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
            )
        return True

    if decision == "photo_content":
        await intent_router.dispatch(
            intent=Intent.PHOTO_CONTENT,
            tenant_id=tenant_id,
            trigger_source="line",
            trigger_payload={
                "line_message_id": pending.get("line_message_id", ""),
                "photo_url": pending.get("photo_url", ""),
                "reply_token": pending.get("reply_token", ""),
            },
        )
        return True

    if business_consultant is None:
        return True

    reply_msg = await business_consultant.build_reply(
        tenant_id=tenant_id,
        message=(
            "使用者剛上傳了一份待判斷內容，摘要如下："
            + pending.get("summary", "")
            + "。接著使用者想先一起討論："
            + line_text
        ),
    )
    repo.save_conversation(
        tenant_id=tenant_id,
        role="ai",
        content=reply_msg.get("text", ""),
        conversation_type=CONSULTATION_CONVERSATION_TYPE,
    )
    if settings.LINE_CHANNEL_ACCESS_TOKEN:
        await push_line_messages(
            to=line_user_id,
            messages=[reply_msg],
            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
        )
    return True





@router.post("/line")

async def line_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(default=""),
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    body = await request.body()
    # Signature verification
    if settings.APP_ENV != "test":
        if not settings.LINE_CHANNEL_SECRET:
            logger.error("LINE webhook invoked without LINE_CHANNEL_SECRET configured")
            raise HTTPException(status_code=503, detail="LINE webhook misconfigured")
        if not _verify_line_signature(body, settings.LINE_CHANNEL_SECRET, x_line_signature):
            raise HTTPException(status_code=400, detail="Invalid LINE signature")



    import json



    payload: dict[str, Any] = json.loads(body)

    events: list[dict[str, Any]] = payload.get("events", [])



    repo: KachuRepository = request.app.state.repository

    agentOS_client: AgentOSClient = request.app.state.agentOS_client

    approval_bridge: ApprovalBridge = request.app.state.approval_bridge

    intent_router: IntentRouter = request.app.state.intent_router

    onboarding_flow: OnboardingFlow = request.app.state.onboarding_flow

    memory_manager: MemoryManager = request.app.state.memory_manager

    context_brief_manager = request.app.state.context_brief_manager

    business_consultant: BusinessConsultant = request.app.state.business_consultant



    for event in events:

        background_tasks.add_task(

            _handle_event_logged,

            event=event,

            repo=repo,

            agentOS_client=agentOS_client,

            approval_bridge=approval_bridge,

            intent_router=intent_router,

            onboarding_flow=onboarding_flow,

            memory_manager=memory_manager,

            context_brief_manager=context_brief_manager,

            business_consultant=business_consultant,

            settings=settings,

        )



    return {"status": "ok"}





async def _handle_event(

    event: dict[str, Any],

    repo: KachuRepository,

    agentOS_client: AgentOSClient,

    approval_bridge: ApprovalBridge,

    intent_router: IntentRouter,

    onboarding_flow: OnboardingFlow,

    memory_manager: MemoryManager,

    settings: Settings,

    context_brief_manager=None,

    business_consultant: BusinessConsultant | None = None,

) -> None:

    event_type = event.get("type")

    source = event.get("source", {})

    line_user_id: str = source.get("userId", "")
    knowledge_capture = KnowledgeCaptureService(
        repo,
        settings,
        memory_manager=memory_manager,
        context_brief_manager=context_brief_manager,
    )



    # Identify if this is the boss or a customer

    is_boss = bool(settings.LINE_BOSS_USER_ID and line_user_id == settings.LINE_BOSS_USER_ID)

    # For single-tenant MVP: boss's user ID is both the tenant_id and the boss identifier

    tenant_id = settings.LINE_BOSS_USER_ID if is_boss else (settings.LINE_BOSS_USER_ID or line_user_id)



    if event_type == "postback":

        data = event.get("postback", {}).get("data", "")

        params = _parse_postback_data(data)

        action_raw = params.get("action", "")

        run_id = params.get("run_id", "")

        pb_tenant_id = params.get("tenant_id", tenant_id)



        # Handle CTA trigger_workflow postback (e.g., from GA4 report button)
        if action_raw == "trigger_workflow":
            workflow_name = params.get("workflow", "")
            intent_name = params.get("intent", "")
            topic = params.get("topic", "")
            if intent_router:
                workflow_intent_map = {
                    "kachu_google_post": Intent.GOOGLE_POST,
                    "kachu_knowledge_update": Intent.KNOWLEDGE_UPDATE,
                    "kachu_ga4_report": Intent.GA4_REPORT,
                    "kachu_review_reply": Intent.REVIEW_REPLY,
                }
                mapped_intent = workflow_intent_map.get(workflow_name)
                if mapped_intent is None and intent_name:
                    try:
                        mapped_intent = Intent(intent_name)
                    except ValueError:
                        mapped_intent = None
                if mapped_intent:
                    await intent_router.dispatch(
                        intent=mapped_intent,
                        tenant_id=pb_tenant_id,
                        trigger_source="line_cta",
                        trigger_payload={
                            "message": topic,
                            "triggered_by": workflow_name or intent_name,
                        },
                    )
            return

        # Handle runtime control actions: cancel / retry / replay
        if action_raw == "cancel_run":
            task_id = params.get("task_id", "")
            if task_id:
                try:
                    await agentOS_client.cancel_task(task_id)
                    if settings.LINE_CHANNEL_ACCESS_TOKEN:
                        await push_line_messages(
                            to=line_user_id,
                            messages=[{"type": "text", "text": "✅ 任務已取消。"}],
                            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                        )
                except httpx.HTTPError as exc:
                    logger.error("cancel_run failed: %s", exc)
            return

        if action_raw == "retry_run":
            if run_id:
                try:
                    await agentOS_client.retry_run(run_id)
                    if settings.LINE_CHANNEL_ACCESS_TOKEN:
                        await push_line_messages(
                            to=line_user_id,
                            messages=[{"type": "text", "text": "🔄 已重試，請稍候…"}],
                            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                        )
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.error("retry_run failed: %s", exc)
            return

        if action_raw == "replay_run":
            if run_id:
                try:
                    await agentOS_client.replay_run(run_id)
                    if settings.LINE_CHANNEL_ACCESS_TOKEN:
                        await push_line_messages(
                            to=line_user_id,
                            messages=[{"type": "text", "text": "▶️ 已重新執行，請稍候…"}],
                            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                        )
                except (httpx.HTTPError, ValidationError) as exc:
                    logger.error("replay_run failed: %s", exc)
            return

        if action_raw == "asset_intent":
            decision = params.get("decision", "")
            pending = repo.get_shared_context(pb_tenant_id, _PENDING_BOSS_ASSET_INTENT)
            if not pending:
                logger.warning("asset_intent postback but no pending context for tenant=%s", pb_tenant_id)
                return
            _clear_pending_asset_context(repo, pb_tenant_id)
            if decision == "photo_content":
                await intent_router.dispatch(
                    intent=Intent.PHOTO_CONTENT,
                    tenant_id=pb_tenant_id,
                    trigger_source="line",
                    trigger_payload={
                        "line_message_id": pending.get("line_message_id", ""),
                        "photo_url": pending.get("photo_url", ""),
                        "reply_token": "",
                    },
                )
            elif decision == "knowledge":
                kb_svc = KnowledgeCaptureService(
                    repo,
                    settings,
                    memory_manager=memory_manager,
                    context_brief_manager=context_brief_manager,
                )
                messages = await kb_svc.capture_knowledge_text(
                    tenant_id=pb_tenant_id,
                    content=pending.get("knowledge_text", ""),
                    source_type=pending.get("source_type", "document"),
                    source_id=pending.get("source_id"),
                    ack_text="好，我把這份內容存入品牌資料了。",
                )
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=messages,
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
            elif decision == "consult":
                if business_consultant and settings.LINE_CHANNEL_ACCESS_TOKEN:
                    summary_text = pending.get("summary", "")
                    consult_msg = f"老闆上傳了一份內容，摘要：{summary_text}，想先討論如何使用。" if summary_text else "老闆上傳了一份內容，想先討論如何使用。"
                    reply_msg = await business_consultant.build_reply(
                        tenant_id=pb_tenant_id,
                        message=consult_msg,
                    )
                    await push_line_messages(
                        to=line_user_id,
                        messages=[reply_msg],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
            else:
                logger.warning("asset_intent: unknown decision=%s", decision)
            return

        if action_raw == "schedule_publish":
            pending = repo.get_pending_approval_by_run_id(run_id)
            if pending is None:
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message("找不到這份待發布草稿，請重新產生一次。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            if pending.workflow_type not in _SCHEDULE_SUPPORTED_WORKFLOWS:
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message("這種確認卡目前還不支援 LINE 排程發布。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            try:
                draft_content = json.loads(pending.draft_content or "{}")
            except json.JSONDecodeError:
                draft_content = {}
            if pending.workflow_type in ("kachu_photo_content", "photo_content") and run_id and not draft_content.get("image_url"):
                draft_content["image_url"] = _build_photo_preview_url(
                    settings.KACHU_BASE_URL,
                    run_id,
                )
            _clear_pending_schedule_contexts(repo, pb_tenant_id)
            repo.save_shared_context(
                tenant_id=pb_tenant_id,
                context_type=_PENDING_SCHEDULE_REQUEST,
                content={
                    "run_id": run_id,
                    "workflow_type": pending.workflow_type,
                    "draft_content": draft_content,
                    "selected_platforms": _derive_schedule_platforms(pending.workflow_type, draft_content),
                },
                ttl_hours=24,
            )
            if settings.LINE_CHANNEL_ACCESS_TOKEN:
                await push_line_messages(
                    to=line_user_id,
                    messages=[_schedule_prompt_message()],
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            return

        if action_raw == "confirm_schedule_publish":
            pending = repo.get_shared_context(pb_tenant_id, _PENDING_SCHEDULE_CONFIRMATION)
            if not pending or pending.get("run_id") != run_id:
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message("這筆排程確認已失效，請重新點一次排程發布。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            scheduled_for = str(pending.get("scheduled_for", "")).strip()
            if not scheduled_for:
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message("排程時間遺失了，請重新點一次排程發布。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            deferred = await approval_bridge.defer_with_schedule(
                run_id=run_id,
                tenant_id=pb_tenant_id,
                actor_line_id=line_user_id,
                scheduled_for=scheduled_for,
            )
            if not deferred:
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message("這份草稿目前無法建立排程，請重新產生一次。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            repo.create_scheduled_publish(
                tenant_id=pb_tenant_id,
                source_run_id=run_id,
                workflow_type=str(pending.get("workflow_type", "")),
                selected_platforms=list(pending.get("selected_platforms") or []),
                draft_content={
                    **dict(pending.get("draft_content") or {}),
                    **(
                        {
                            "image_url": _build_photo_preview_url(settings.KACHU_BASE_URL, run_id),
                        }
                        if str(pending.get("workflow_type", "")) in ("kachu_photo_content", "photo_content")
                        and run_id
                        and not dict(pending.get("draft_content") or {}).get("image_url")
                        else {}
                    ),
                },
                scheduled_for=datetime.fromisoformat(scheduled_for),
                actor_line_id=line_user_id,
            )
            _clear_pending_schedule_contexts(repo, pb_tenant_id)
            if settings.LINE_CHANNEL_ACCESS_TOKEN:
                scheduled_label = str(pending.get("scheduled_label", "")).strip() or scheduled_for
                await push_line_messages(
                    to=line_user_id,
                    messages=[text_message(f"好，我會在 {scheduled_label} 幫你自動發布。")],
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            return

        if action_raw == "cancel_schedule_publish":
            pending = repo.get_shared_context(pb_tenant_id, _PENDING_SCHEDULE_CONFIRMATION)
            _clear_pending_schedule_contexts(repo, pb_tenant_id)
            if settings.LINE_CHANNEL_ACCESS_TOKEN:
                messages: list[dict[str, Any]] = [text_message("好，先取消這次排程。你可以重新輸入時間，或直接按立即發布。")]
                if pending and pending.get("run_id") == run_id:
                    repo.save_shared_context(
                        tenant_id=pb_tenant_id,
                        context_type=_PENDING_SCHEDULE_REQUEST,
                        content={
                            "run_id": run_id,
                            "workflow_type": pending.get("workflow_type", ""),
                            "draft_content": pending.get("draft_content") or {},
                            "selected_platforms": pending.get("selected_platforms") or [],
                        },
                        ttl_hours=24,
                    )
                    messages.append(_schedule_prompt_message())
                await push_line_messages(
                    to=line_user_id,
                    messages=messages,
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            return

        # ── Flow C: comment reply / hide postbacks ─────────────────────────
        if action_raw in ("reply_comment", "hide_comment"):
            comment_id = params.get("comment_id", "")
            platform = params.get("platform", "fb")  # "fb" or "ig"
            object_id = params.get("object_id", "")

            if not comment_id:
                logger.warning("comment postback missing comment_id: %s", params)
                return

            base_url = settings.KACHU_BASE_URL if hasattr(settings, "KACHU_BASE_URL") else "http://localhost:8000"
            api_key = getattr(settings, "KACHU_INTERNAL_API_KEY", "") or getattr(settings, "AGENTOS_API_KEY", "")
            headers = {"X-API-Key": api_key} if api_key else {}

            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    if action_raw == "reply_comment":
                        # The reply_draft was embedded in comment context; we need to retrieve it
                        # from shared_context saved at notification time
                        draft_key = f"comment_draft:{comment_id}"
                        draft_ctx = repo.get_shared_context(pb_tenant_id, draft_key)
                        reply_text = (draft_ctx or {}).get("draft", "")
                        if not reply_text:
                            logger.warning("reply_comment: no draft found for comment_id=%s", comment_id)
                            if settings.LINE_CHANNEL_ACCESS_TOKEN:
                                await push_line_messages(
                                    to=line_user_id,
                                    messages=[text_message("找不到對應的回覆草稿，請稍後再試。")],
                                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                                )
                            return

                        endpoint = "/tools/fb-reply-comment" if platform == "fb" else "/tools/ig-reply-comment"
                        payload = {
                            "tenant_id": pb_tenant_id,
                            "comment_id": comment_id,
                            "message": reply_text,
                        }
                        resp = await client.post(f"{base_url}{endpoint}", json=payload, headers=headers)
                        resp.raise_for_status()
                        ack = "✅ 已回覆這則留言！" if platform == "fb" else "✅ 已回覆這則 IG 留言！"

                    else:  # hide_comment
                        endpoint = "/tools/fb-hide-comment" if platform == "fb" else "/tools/ig-hide-comment"
                        payload = {
                            "tenant_id": pb_tenant_id,
                            "comment_id": comment_id,
                            "is_hidden": True,
                        } if platform == "fb" else {
                            "tenant_id": pb_tenant_id,
                            "comment_id": comment_id,
                            "hide": True,
                        }
                        resp = await client.post(f"{base_url}{endpoint}", json=payload, headers=headers)
                        resp.raise_for_status()
                        ack = "🙈 已隱藏這則留言。"

                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message(ack)],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
            except httpx.HTTPError as exc:
                logger.error("comment postback action=%s comment_id=%s failed: %s", action_raw, comment_id, exc)
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message("操作失敗，請稍後再試。")],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
            return

        _clear_pending_schedule_contexts(repo, pb_tenant_id)

        try:

            action = ApprovalAction(action_raw)

        except ValueError:

            logger.warning("Unknown postback action: %s", action_raw)

            return



        await approval_bridge.handle_postback(

            run_id=run_id,

            tenant_id=pb_tenant_id,

            action=action,

            actor_line_id=line_user_id,

        )

        return



    if event_type == "message":

        message = event.get("message", {})

        msg_type = message.get("type", "text")

        reply_token = event.get("replyToken", "")



        # ?�?� Customer message ??LINE FAQ workflow ?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�?�

        if not is_boss:

            text: str = message.get("text", "")

            if text:

                repo.save_conversation(

                    tenant_id=tenant_id,

                    role="customer",

                    content=text,

                    conversation_type="general",

                )

                await intent_router.dispatch(

                    intent=Intent.FAQ_QUERY,

                    tenant_id=tenant_id,

                    trigger_source="line_customer",

                    trigger_payload={

                        "customer_line_id": line_user_id,

                        "message": text,

                    },

                )

            return



        # ?�?� Boss: check for active edit session (highest priority) ?�?�?�?�?�?�?�?�?�?�?�?�

        if msg_type == "text":

            active_edit = repo.get_active_edit_session(tenant_id)

            if active_edit:

                boss_text: str = message.get("text", "")

                await _handle_edit_reply(

                    edit_session=active_edit,

                    text=boss_text,

                    tenant_id=tenant_id,

                    line_user_id=line_user_id,

                    repo=repo,

                    approval_bridge=approval_bridge,

                    memory_manager=memory_manager,

                    settings=settings,

                )

                return



        # ── Boss message: DAY 0 onboarding takes priority ──────────────────────

        if onboarding_flow.is_in_onboarding(tenant_id):

            content = message.get("text", "") or message.get("id", "")

            # Download bytes for image/file types so the parser can work

            content_bytes: bytes | None = None

            mime_type = "image/jpeg"

            if msg_type == "image" and settings.LINE_CHANNEL_ACCESS_TOKEN and content:

                try:

                    content_bytes = await _download_line_content_with_retry(

                        content, settings.LINE_CHANNEL_ACCESS_TOKEN

                    )

                except httpx.HTTPError as exc:

                    logger.warning("Could not download onboarding image: %s", exc)

                    await _notify_processing_failure(

                        line_user_id=line_user_id,

                        settings=settings,

                        text="圖片下載失敗，請稍後重新上傳一次。",

                    )

                    return

            elif msg_type == "file":

                # LINE file messages: content contains the message_id, bytes fetched via content API

                if settings.LINE_CHANNEL_ACCESS_TOKEN and content:

                    try:

                        content_bytes = await _download_line_content_with_retry(

                            content, settings.LINE_CHANNEL_ACCESS_TOKEN

                        )

                        mime_type = "application/pdf"  # default; will parse as generic doc

                    except httpx.HTTPError as exc:

                        logger.warning("Could not download onboarding file: %s", exc)

                        await _notify_processing_failure(

                            line_user_id=line_user_id,

                            settings=settings,

                            text="檔案下載失敗，請稍後重新上傳一次。",

                        )

                        return

            reply_messages = await onboarding_flow.handle_message(

                tenant_id=tenant_id,

                msg_type=msg_type,

                content=content,

                content_bytes=content_bytes,

                mime_type=mime_type,

            )

            if reply_messages and settings.LINE_CHANNEL_ACCESS_TOKEN:

                try:

                    await push_line_messages(

                        to=line_user_id,

                        messages=reply_messages,

                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

                    )

                except httpx.HTTPError as exc:

                    logger.error("Failed to push onboarding message: %s", exc)

            return



        if msg_type == "text":

            boss_text_2: str = message.get("text", "")
            line_message_id = str(message.get("id", "")).strip()

            pending_handled = await _handle_pending_asset_reply(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                line_text=boss_text_2,
                intent_router=intent_router,
                business_consultant=business_consultant,
                settings=settings,
                knowledge_capture=knowledge_capture,
            )
            if pending_handled:
                return

            pending_text_handled = await _handle_pending_text_intent_reply(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                line_text=boss_text_2,
                line_message_id=line_message_id,
                intent_router=intent_router,
                business_consultant=business_consultant,
                settings=settings,
            )
            if pending_text_handled:
                return

            pending_schedule_handled = await _handle_pending_schedule_reply(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                line_text=boss_text_2,
                settings=settings,
            )
            if pending_schedule_handled:
                return

            if _should_absorb_explicit_knowledge_text(boss_text_2):
                repo.save_conversation(
                    tenant_id=tenant_id,
                    role="owner",
                    content=boss_text_2,
                    conversation_type=COMMAND_CONVERSATION_TYPE,
                )
                messages = await knowledge_capture.capture_knowledge_text(
                    tenant_id=tenant_id,
                    content=boss_text_2,
                    source_type="conversation",
                    ack_text="我先把這段內容當成品牌資料記住了。",
                )
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=messages,
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            route = BossRouteDecision()
            if intent_router is not None and hasattr(intent_router, "plan_boss_message"):
                route = await intent_router.plan_boss_message(boss_text_2)
            owner_conversation_type = "general" if route.small_talk else (
                COMMAND_CONVERSATION_TYPE if route.mode == BossRouteMode.EXECUTE else CONSULTATION_CONVERSATION_TYPE
            )
            repo.save_conversation(
                tenant_id=tenant_id,
                role="owner",
                content=boss_text_2,
                conversation_type=owner_conversation_type,
            )

            if owner_conversation_type == COMMAND_CONVERSATION_TYPE and context_brief_manager is not None:
                await context_brief_manager.refresh_briefs(
                    tenant_id,
                    reason="boss_command_message",
                )

            if route.mode == BossRouteMode.CLARIFY:
                repo.save_shared_context(
                    tenant_id=tenant_id,
                    context_type=_PENDING_BOSS_TEXT_INTENT,
                    content={
                        "intent": str(route.intent),
                        "message": boss_text_2,
                        "topic": route.topic,
                        "clarify_question": route.clarify_question,
                    },
                    ttl_hours=6,
                )
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[text_message(route.clarify_question)],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            if route.mode == BossRouteMode.EXECUTE:
                await intent_router.dispatch(
                    intent=route.intent,
                    tenant_id=tenant_id,
                    trigger_source="line",
                    trigger_payload={
                        "message": boss_text_2,
                        "topic": route.topic,
                        "line_message_id": line_message_id,
                    },
                )
                return

            if route.small_talk and route.intent == Intent.GENERAL_CHAT:
                reply_text = _build_small_talk_reply(boss_text_2)
                repo.save_conversation(
                    tenant_id=tenant_id,
                    role="ai",
                    content=reply_text,
                    conversation_type="general",
                )
                await push_line_messages(
                    to=settings.LINE_BOSS_USER_ID,
                    messages=[text_message(reply_text)],
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
                return

            logger.info("General chat from boss: %s", boss_text_2[:80])
            try:
                if business_consultant is None:
                    raise RuntimeError("BusinessConsultant unavailable")
                reply_msg = await business_consultant.build_reply(
                    tenant_id=tenant_id,
                    message=boss_text_2,
                )
                repo.save_conversation(
                    tenant_id=tenant_id,
                    role="ai",
                    content=reply_msg.get("text", ""),
                    conversation_type=CONSULTATION_CONVERSATION_TYPE,
                )
                await push_line_messages(
                    to=settings.LINE_BOSS_USER_ID,
                    messages=[reply_msg],
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            except (httpx.HTTPError, ValueError, RuntimeError) as _gp_err:
                logger.warning("BusinessConsultant failed, sending fallback guidance: %s", _gp_err)
                await _notify_processing_failure(
                    line_user_id=line_user_id,
                    settings=settings,
                    text="目前暫時無法解析這則指令。請直接輸入想做的事，例如：幫我發一篇 Google 動態。",
                )
            return

        if msg_type in {"image", "file"}:

            line_message_id = message.get("id", "")

            photo_url = ""
            content_bytes: bytes | None = None
            mime_type = "image/jpeg" if msg_type == "image" else "application/pdf"

            if settings.LINE_CHANNEL_ACCESS_TOKEN and line_message_id:

                try:

                    content_bytes = await _download_line_content_with_retry(

                        line_message_id, settings.LINE_CHANNEL_ACCESS_TOKEN

                    )
                    if msg_type == "image":
                        photo_url = "data:image/jpeg;base64," + base64.b64encode(content_bytes).decode()

                except httpx.HTTPError as _exc:

                    logger.warning("Failed to download LINE %s %s: %s", msg_type, line_message_id, _exc)

                    await _notify_processing_failure(

                        line_user_id=line_user_id,

                        settings=settings,

                        text="檔案下載失敗，請重新上傳後再試一次。",

                    )

                    return

            extracted_text = ""
            source_type = "document"
            if content_bytes is not None and settings is not None:
                parse_result = await document_parser.parse_document(
                    msg_type=msg_type,
                    content_bytes=content_bytes,
                    content_text=None,
                    mime_type=mime_type,
                    settings=settings,
                )
                if not parse_result.needs_manual:
                    extracted_text = parse_result.text
                    source_type = parse_result.source_type

            action, summary, reason = await _classify_media_after_onboarding(
                msg_type=msg_type,
                extracted_text=extracted_text,
                settings=settings,
            )
            logger.info(
                "Boss media routed: tenant=%s type=%s action=%s reason=%s",
                tenant_id,
                msg_type,
                action,
                reason,
            )

            if action == "knowledge":
                knowledge_text = extracted_text or f"[{msg_type} uploaded, message_id={line_message_id}]"
                messages = await knowledge_capture.capture_knowledge_text(
                    tenant_id=tenant_id,
                    content=knowledge_text,
                    source_type=source_type,
                    source_id=line_message_id,
                    ack_text="我先把這份內容當成品牌資料吸收了。",
                )
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=messages,
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            if action == "clarify":
                repo.save_shared_context(
                    tenant_id=tenant_id,
                    context_type=_PENDING_BOSS_ASSET_INTENT,
                    content={
                        "line_message_id": line_message_id,
                        "photo_url": photo_url,
                        "reply_token": reply_token,
                        "knowledge_text": extracted_text or f"[{msg_type} uploaded, message_id={line_message_id}]",
                        "source_type": source_type,
                        "source_id": line_message_id,
                        "summary": summary,
                    },
                    ttl_hours=6,
                )
                if settings.LINE_CHANNEL_ACCESS_TOKEN:
                    await push_line_messages(
                        to=line_user_id,
                        messages=[_build_asset_clarification_message(summary)],
                        access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                    )
                return

            await intent_router.dispatch(

                intent=Intent.PHOTO_CONTENT,

                tenant_id=tenant_id,

                trigger_source="line",

                trigger_payload={

                    "line_message_id": line_message_id,

                    "photo_url": photo_url,

                    "reply_token": reply_token,

                },

            )
            return





async def _handle_edit_reply(

    edit_session,

    text: str,

    tenant_id: str,

    line_user_id: str,

    repo: KachuRepository,

    approval_bridge: ApprovalBridge,

    memory_manager: MemoryManager,

    settings: Settings,

) -> None:

    """Handle boss feedback during an active edit session.

    New flow: boss types a one-line feedback → LLM regenerates both drafts →
    push a new approval Flex.  Legacy waiting_ig/waiting_google steps are no
    longer created but are handled for backward-compatibility.
    """

    import re

    from ..llm.client import generate_text

    from .flex_builder import build_photo_content_flex



    # ── Legacy two-step path (waiting_ig / waiting_google) ────────────────────
    # Kept so that any in-flight sessions created before this deploy still work.

    skip_keywords = {"跳過", "skip", "next", "略過"}



    if edit_session.step == "waiting_ig":

        if text.strip() not in skip_keywords:

            memory_manager.store_preference(

                tenant_id=tenant_id,

                platform="ig_fb",

                original_draft=edit_session.original_ig_draft,

                edited_draft=text,

                run_id=edit_session.run_id,

            )

            repo.update_edit_session_draft(edit_session.id, "ig_fb", text)

        else:

            repo.update_edit_session_draft(edit_session.id, "ig_fb", edit_session.original_ig_draft)

        repo.advance_edit_session(edit_session.id, "waiting_google")

        if settings.LINE_CHANNEL_ACCESS_TOKEN:

            try:

                await push_line_messages(

                    to=line_user_id,

                    messages=[text_message("✅ IG 文本已儲存\n\n請輸入 Google 商家文本：\n（如果不需要修改，輸入「跳過」）")],

                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

                )

            except httpx.HTTPError as exc:

                logger.error("Failed to push Google edit prompt: %s", exc)

        return



    if edit_session.step == "waiting_google":

        if text.strip() not in skip_keywords:

            memory_manager.store_preference(

                tenant_id=tenant_id,

                platform="google",

                original_draft=edit_session.original_google_draft,

                edited_draft=text,

                run_id=edit_session.run_id,

            )

            repo.update_edit_session_draft(edit_session.id, "google", text)

        else:

            repo.update_edit_session_draft(edit_session.id, "google", edit_session.original_google_draft)

        updated = repo.get_active_edit_session(tenant_id)

        edited_ig = updated.edited_ig_draft if updated else edit_session.original_ig_draft

        edited_google = updated.edited_google_draft if updated else edit_session.original_google_draft

        submitted = await approval_bridge.complete_edit_and_approve(

            run_id=edit_session.run_id,

            actor_line_id=line_user_id,

            edited_ig_draft=edited_ig,

            edited_google_draft=edited_google,

        )

        if submitted:

            repo.complete_edit_session(edit_session.id)

        memory_manager.record_episode(

            tenant_id=tenant_id,

            workflow_type="photo_content",

            outcome="edited",

            context_summary={"run_id": edit_session.run_id},

        )

        if settings.LINE_CHANNEL_ACCESS_TOKEN:

            try:

                await push_line_messages(

                    to=line_user_id,

                    messages=[text_message("✅ 已儲存並送出發布。")],

                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

                )

            except httpx.HTTPError as exc:

                logger.error("Failed to push edit completion message: %s", exc)

        return



    # ── New single-step feedback path (waiting_feedback) ──────────────────────

    ig_draft = edit_session.original_ig_draft

    google_draft = edit_session.original_google_draft



    # 1. LLM regenerate

    prompt = (

        "你是一位專業社群媒體文案撰寫師。根據老闆的修改意見，重新生成貼文草稿。\n\n"

        f"【原始 IG/Facebook 草稿】\n{ig_draft}\n\n"

        f"【原始 Google 商家草稿】\n{google_draft}\n\n"

        f"【修改意見】\n{text}\n\n"

        "請以下列格式回覆，嚴格遵守格式，不要有其他文字：\n"

        "===IG_FB===\n（新的 IG/Facebook 文案）\n"

        "===GOOGLE===\n（新的 Google 商家文案）\n"

        "===END==="

    )

    new_ig = ig_draft

    new_google = google_draft

    try:

        llm_resp = await generate_text(

            prompt=prompt,

            model=settings.LITELLM_MODEL,

            api_key=settings.GOOGLE_AI_API_KEY,

            openai_api_key=settings.OPENAI_API_KEY,

        )

        ig_match = re.search(r"===IG_FB===\s*(.*?)\s*===GOOGLE===", llm_resp, re.DOTALL)

        google_match = re.search(r"===GOOGLE===\s*(.*?)\s*===END===", llm_resp, re.DOTALL)

        if ig_match:

            new_ig = ig_match.group(1).strip()

        if google_match:

            new_google = google_match.group(1).strip()

    except Exception:  # noqa: BLE001

        logger.exception("LLM regen failed during edit feedback; falling back to original drafts")



    # 2. Update local approval task with new drafts so approve postback sends the right content

    repo.update_approval_draft_content(

        run_id=edit_session.run_id,

        draft_content={"ig_fb": new_ig, "google": new_google},

    )



    # 3. Store preference memory

    memory_manager.store_preference(

        tenant_id=tenant_id,

        platform="ig_fb",

        original_draft=ig_draft,

        edited_draft=new_ig,

        run_id=edit_session.run_id,

    )



    # 4. Complete edit session

    repo.complete_edit_session(edit_session.id)



    # 5. Record episode

    memory_manager.record_episode(

        tenant_id=tenant_id,

        workflow_type="photo_content",

        outcome="edited",

        context_summary={"run_id": edit_session.run_id},

    )



    # 6. Push regenerated Flex for re-approval

    if settings.LINE_CHANNEL_ACCESS_TOKEN:

        flex_bubble = build_photo_content_flex(

            run_id=edit_session.run_id,

            tenant_id=tenant_id,

            drafts={"ig_fb": new_ig, "google": new_google},

        )

        try:

            await push_line_messages(

                to=line_user_id,

                messages=[

                    text_message("✏️ 重新生成好了，請確認這個版本："),

                    {

                        "type": "flex",

                        "altText": "✏️ 重新生成的草稿，請確認",

                        "contents": flex_bubble,

                    },

                ],

                access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

            )

        except httpx.HTTPError as exc:

            logger.error("Failed to push regenerated Flex: %s", exc)



async def _download_line_image(message_id: str, access_token: str) -> bytes:

    """Download image binary from LINE Content API."""

    async with httpx.AsyncClient(timeout=15.0) as client:

        resp = await client.get(

            f"https://api-data.line.me/v2/bot/message/{message_id}/content",

            headers={"Authorization": f"Bearer {access_token}"},

        )

        resp.raise_for_status()

        return resp.content
