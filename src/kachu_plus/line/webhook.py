from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import logging
import re
from urllib.parse import parse_qs
from typing import Any

import httpx
from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request

from kachu_plus.approval import ApprovalBridge
from kachu_plus.consult_context import ConsultContextBuilder
from kachu_plus.config import get_settings
from kachu_plus.crypto import decrypt_field
from kachu_plus.dialogue_state import DialogueStateResolver
from kachu_plus.intent_router import classify_boss_message
from kachu_plus.line.flex_builder import (
    build_asset_intent_prompt_message,
    build_external_reply_flex,
    build_google_post_flex,
    build_photo_content_flex,
    build_planned_content_flex,
    build_review_reply_flex,
)
from kachu_plus.memory_promotion import ConversationMemoryPromoter
from kachu_plus.meta import (
    MetaConnectorError,
    MetaInsightsService,
    MetaOAuthFlowService,
    build_meta_manage_url,
    deliver_meta_insights_report,
    summarize_meta_insights,
)
from kachu_plus.models import ApprovalAction, BossRouteMode, ExecutionTaskResult
from kachu_plus.onboarding.flow import OnboardingFlow
from kachu_plus.line.push import push_line_messages, resolve_tenant_line_recipients, text_message
from kachu_plus.suggestions import handle_suggestion_action
from kachu_plus.publishing import publish_content_bundle, publish_content_bundle_succeeded, publish_meta_reply
from kachu_plus.tools_router import analyze_photo_payload, build_content_drafts, build_content_plan_payload, run_line_faq_flow
from kachu_plus.services import (
    AgentOSTaskDispatcher,
    ContextBriefManager,
    ConversationLearningService,
    LLMConsultant,
    MemoryManager,
    PostTaskReviewService,
    SleepCustomerQueryService,
    UnsupportedExecutionIntentError,
)
from kachu_plus.website_knowledge import WebsiteKnowledgeIngestionService, format_website_ingestion_reply

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["line"])

_PENDING_ASSET_INTENT_TTL = timedelta(minutes=30)


def _extract_text_messages(messages: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for message in messages:
        if str(message.get("type", "") or "") != "text":
            continue
        text = str(message.get("text", "") or "").strip()
        if text:
            texts.append(text)
    return texts


def _record_inbound_conversation(
    *,
    app: Any | None = None,
    repo: Any,
    tenant_id: str,
    line_user_id: str,
    actor_role: str,
    conversation_kind: str,
    msg_type: str,
    text: str,
    line_message_id: str,
    metadata: dict[str, object] | None = None,
) -> Any:
    content_text = text.strip()
    if not content_text:
        content_text = f"[line {msg_type or 'message'}:{line_message_id or 'no-id'}]"
    conversation = repo.save_conversation(
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        actor_role=actor_role,
        channel_type="line",
        conversation_kind=conversation_kind,
        content_text=content_text,
        source_message_id=line_message_id,
        metadata=metadata,
    )
    if app is not None and actor_role == "boss":
        _get_conversation_learning_service(app, repo).absorb_conversation(
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation=conversation,
        )
    return conversation


async def _push_and_record_texts(
    *,
    repo: Any,
    tenant_id: str,
    line_user_id: str,
    conversation_kind: str,
    messages: list[dict[str, Any]],
    access_token: str,
    related_task_id: str = "",
    related_run_id: str = "",
    metadata: dict[str, object] | None = None,
) -> None:
    for text in _extract_text_messages(messages):
        repo.save_conversation(
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="ai",
            channel_type="line",
            conversation_kind=conversation_kind,
            content_text=text,
            related_task_id=related_task_id,
            related_run_id=related_run_id,
            metadata=metadata,
        )
    await _push_safe(
        to=line_user_id,
        messages=messages,
        access_token=access_token,
        tenant_id=tenant_id,
    )


def _line_event_dedupe_key(tenant_id: str, event: dict[str, Any]) -> str:
    event_type = str(event.get("type", "") or "unknown").strip() or "unknown"
    webhook_event_id = str(event.get("webhookEventId", "") or "").strip()
    if webhook_event_id:
        return f"line:{tenant_id}:{event_type}:event:{webhook_event_id}"

    source = event.get("source", {}) if isinstance(event.get("source", {}), dict) else {}
    user_id = str(source.get("userId", "") or "").strip()
    timestamp = str(event.get("timestamp", "") or "").strip()
    reply_token = str(event.get("replyToken", "") or "").strip()
    message = event.get("message", {}) if isinstance(event.get("message", {}), dict) else {}
    message_id = str(message.get("id", "") or "").strip()
    if message_id:
        return f"line:{tenant_id}:{event_type}:message:{message_id}"

    postback = event.get("postback", {}) if isinstance(event.get("postback", {}), dict) else {}
    postback_data = str(postback.get("data", "") or "").strip()
    if timestamp and (reply_token or postback_data or user_id):
        return f"line:{tenant_id}:{event_type}:ts:{timestamp}:user:{user_id}:reply:{reply_token}:postback:{postback_data}"

    canonical = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"line:{tenant_id}:{event_type}:hash:{digest}"


def _line_event_occurred_at(event: dict[str, Any]) -> Any:
    timestamp = event.get("timestamp")
    try:
        timestamp_int = int(timestamp)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp_int / 1000, tz=timezone.utc)


def _line_event_external_ids(event: dict[str, Any]) -> tuple[str, str, str]:
    source = event.get("source", {}) if isinstance(event.get("source", {}), dict) else {}
    message = event.get("message", {}) if isinstance(event.get("message", {}), dict) else {}
    postback = event.get("postback", {}) if isinstance(event.get("postback", {}), dict) else {}
    external_event_id = str(event.get("webhookEventId", "") or message.get("id", "") or "").strip()
    external_user_id = str(source.get("userId", "") or "").strip()
    external_thread_id = str(message.get("id", "") or postback.get("data", "") or external_event_id).strip()
    return external_event_id, external_user_id, external_thread_id


def _get_execute_dispatcher(request: Request) -> AgentOSTaskDispatcher:
    dispatcher = getattr(request.app.state, "execute_dispatcher", None)
    if dispatcher is None:
        dispatcher = AgentOSTaskDispatcher(get_settings())
        request.app.state.execute_dispatcher = dispatcher
    return dispatcher


def _get_consultant(request: Request) -> LLMConsultant:
    consultant = getattr(request.app.state, "consultant", None)
    if consultant is None:
        consultant = LLMConsultant(get_settings())
        request.app.state.consultant = consultant
    return consultant


def _get_sleep_query_service(request: Request, repo: Any) -> SleepCustomerQueryService:
    service = getattr(request.app.state, "sleep_query_service", None)
    if service is None:
        service = SleepCustomerQueryService(repo)
        request.app.state.sleep_query_service = service
    return service


def _get_meta_insights_service(app: Any, repo: Any) -> MetaInsightsService:
    service = getattr(app.state, "meta_insights_service", None)
    if service is None:
        service = MetaInsightsService(repo, app.state.settings)
        app.state.meta_insights_service = service
    return service


def _get_meta_oauth_service(app: Any, repo: Any) -> MetaOAuthFlowService:
    service = getattr(app.state, "meta_oauth_flow_service", None)
    if service is None:
        service = MetaOAuthFlowService(repo, app.state.settings)
        app.state.meta_oauth_flow_service = service
    return service


def _get_approval_bridge(request: Request, repo: Any) -> ApprovalBridge:
    bridge = getattr(request.app.state, "approval_bridge", None)
    if bridge is None:
        memory = MemoryManager(repo, request.app.state.settings)
        briefs = ContextBriefManager(repo, memory)
        post_task_review = PostTaskReviewService(repo, memory, briefs)
        bridge = ApprovalBridge(_get_execute_dispatcher(request), repo, post_task_review)
        request.app.state.approval_bridge = bridge
    return bridge


def _get_memory_manager(app: Any, repo: Any) -> MemoryManager:
    memory = getattr(app.state, "memory_manager", None)
    if memory is None:
        memory = MemoryManager(repo, app.state.settings)
        app.state.memory_manager = memory
    return memory


def _get_conversation_learning_service(app: Any, repo: Any) -> ConversationLearningService:
    service = getattr(app.state, "conversation_learning_service", None)
    if service is None:
        service = ConversationLearningService(repo, _get_memory_promoter(app, repo))
        app.state.conversation_learning_service = service
    return service


def _get_context_brief_manager(app: Any, repo: Any) -> ContextBriefManager:
    manager = getattr(app.state, "context_brief_manager", None)
    if manager is None:
        manager = ContextBriefManager(repo, _get_memory_manager(app, repo))
        app.state.context_brief_manager = manager
    return manager


def _get_consult_context_builder(app: Any, repo: Any) -> ConsultContextBuilder:
    builder = getattr(app.state, "consult_context_builder", None)
    if builder is None:
        builder = ConsultContextBuilder(
            repo,
            _get_memory_manager(app, repo),
            _get_context_brief_manager(app, repo),
        )
        app.state.consult_context_builder = builder
    return builder


def _get_dialogue_state_resolver(app: Any, repo: Any) -> DialogueStateResolver:
    resolver = getattr(app.state, "dialogue_state_resolver", None)
    if resolver is None:
        resolver = DialogueStateResolver(repo)
        app.state.dialogue_state_resolver = resolver
    return resolver


def _get_memory_promoter(app: Any, repo: Any) -> ConversationMemoryPromoter:
    promoter = getattr(app.state, "conversation_memory_promoter", None)
    if promoter is None:
        promoter = ConversationMemoryPromoter(repo)
        app.state.conversation_memory_promoter = promoter
    return promoter


async def _push_safe(
    *,
    to: str,
    messages: list[dict[str, Any]],
    access_token: str,
    tenant_id: str,
) -> None:
    """
    推播 LINE 訊息；失敗時只記錄 warning，不拋出例外。
    空的 to 或 access_token 時直接略過（dev/test 環境）。
    """
    if not to or not access_token or not messages:
        return
    try:
        await push_line_messages(to=to, messages=messages, access_token=access_token)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "tenant=%s push HTTP error %s: %s",
            tenant_id, exc.response.status_code, exc.response.text[:200],
        )
    except httpx.RequestError as exc:
        logger.warning("tenant=%s push request error: %s", tenant_id, exc)
    except Exception as exc:
        logger.warning("tenant=%s push unexpected error: %s", tenant_id, exc)


async def _download_line_message_content(line_message_id: str, access_token: str) -> bytes:
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://api-data.line.me/v2/bot/message/{line_message_id}/content",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=20.0,
        )
        response.raise_for_status()
        return response.content


async def _handle_tag_management(
    *,
    tenant_id: str,
    text: str,
    line_user_id: str,
    repo: Any,
    channel_access_token: str,
) -> None:
    """
    A-4：標籤管理 intent 處理。
    支援：查看標籤 / 建立標籤 <名稱> / 刪除標籤 <名稱>
    """
    # 查看標籤
    if any(kw in text for kw in ("查看標籤", "我的標籤", "標籤列表", "顯示標籤", "有什麼標籤")):
        tags = repo.list_tags(tenant_id, include_inactive=False)
        if not tags:
            reply = "目前沒有任何標籤。\n\n你可以說「建立標籤 VIP顧客」來新增一個。"
        else:
            tag_list = "\n".join(f"• {t.name}" for t in tags)
            reply = f"目前有 {len(tags)} 個標籤：\n{tag_list}"
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind="boss_command",
            messages=[text_message(reply)],
            access_token=channel_access_token,
        )
        return

    # 建立標籤
    tag_name = _extract_tag_name(text, action="create")
    if tag_name:
        if tag_name:
            try:
                tag = repo.create_tag(tenant_id, name=tag_name)
                reply = f"✅ 標籤「{tag.name}」已建立。"
            except ValueError:
                reply = f"「{tag_name}」這個標籤名稱已存在。"
            except LookupError:
                reply = "建立失敗：找不到此商家，請聯繫客服。"
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="boss_command",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
        return

    # 刪除標籤
    tag_name = _extract_tag_name(text, action="delete")
    if tag_name:
        if tag_name:
            tags = repo.list_tags(tenant_id)
            matching = [t for t in tags if t.name == tag_name]
            if not matching:
                reply = f"找不到標籤「{tag_name}」，請確認名稱是否正確。"
            else:
                try:
                    repo.deactivate_tag(tenant_id, matching[0].id)
                    reply = f"🗑️ 標籤「{tag_name}」已刪除。"
                except LookupError:
                    reply = f"刪除失敗：找不到標籤「{tag_name}」。"
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="boss_command",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
        return

    # fallback：使用說明
    help_text = (
        "我可以幫你管理標籤。\n\n"
        "你可以直接這樣說：\n"
        "• 查看我的標籤\n"
        "• 幫我建一個 VIP 顧客標籤\n"
        "• 刪除標籤 VIP顧客"
    )
    await _push_and_record_texts(
        repo=repo,
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        conversation_kind="boss_command",
        messages=[text_message(help_text)],
        access_token=channel_access_token,
    )


def _extract_tag_name(text: str, *, action: str) -> str:
    action_keywords = {
        "create": ("建立", "新增", "新建", "建"),
        "delete": ("刪除", "移除", "停用"),
    }
    keywords = action_keywords[action]
    quoted_match = re.search(r"[「\"]([^」\"\n]+)[」\"]\s*標籤?", text)
    if quoted_match and any(keyword in text for keyword in keywords):
        return quoted_match.group(1).strip()

    suffix_match = re.search(r"標籤[：:\s]+([^\n]+)$", text)
    if suffix_match and any(keyword in text for keyword in keywords):
        return suffix_match.group(1).strip().strip("「」\"")

    for keyword in keywords:
        prefix_match = re.search(rf"{keyword}(?:一個)?\s+(.+?)\s*標籤$", text)
        if prefix_match:
            return prefix_match.group(1).strip().strip("「」\"")

    return ""


def _build_execute_ack(*, intent_label: str, task: ExecutionTaskResult) -> str:
    if intent_label == "photo_content":
        return "收到照片了，我先幫你分析並起草貼文。整理好後會推播草稿給你確認。"
    if intent_label == "review_reply":
        return "收到，我先幫你整理評論並起草回覆。通常 10 到 30 秒內會把草稿推給你確認。"
    if intent_label == "google_post":
        return "收到，我先幫你起草內容。整理好後會推播草稿給你確認。"
    if intent_label == "knowledge_update":
        return "收到，我先整理這次店家資訊更新，確認後會通知你。"
    if task.waiting_approval:
        return "好的，我幫你處理中。完成後會推播結果或草稿給你確認。"
    return "好的，我幫你處理中，完成後會通知你。"


def _describe_execute_artifact(intent_label: str) -> str:
    mapping = {
        "google_post": "貼文草稿",
        "photo_content": "貼文草稿",
        "review_reply": "評論回覆草稿",
    }
    return mapping.get(intent_label, "任務結果")


def _format_content_plan_reply(plan: dict[str, Any]) -> str:
    proof_points = [str(item).strip() for item in plan.get("proof_points", []) if str(item).strip()]
    platforms = " / ".join(plan.get("selected_platforms", []) or ["ig_fb", "google"])
    return (
        "這是我先幫你整理的內容企劃：\n"
        f"主題：{plan.get('objective', '本週內容')}\n"
        f"角度：{plan.get('campaign_angle', '品牌亮點')}\n"
        f"方向：{plan.get('creative_direction', '')}\n"
        f"重點：{'、'.join(proof_points[:3]) or '把亮點說清楚'}\n"
        f"CTA：{plan.get('call_to_action', '歡迎私訊了解更多。')}\n"
        f"平台：{platforms}\n\n"
        "如果可以，直接回我：照這個企劃寫。"
    )


def _looks_like_plan_to_draft_request(text: str) -> bool:
    normalized = "".join(str(text or "").split())
    triggers = (
        "照這個企劃寫",
        "照這個規劃寫",
        "照剛剛的企劃寫",
        "照剛剛的方向寫",
        "依這個企劃寫",
        "按照企劃寫",
    )
    return any(token in normalized for token in triggers)


def _get_latest_content_plan(repo: Any, *, tenant_id: str, line_user_id: str) -> dict[str, Any] | None:
    conversations = repo.list_recent_conversations(
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        actor_roles=["ai"],
        conversation_kinds=["content_plan"],
        limit=5,
    )
    for conversation in conversations:
        try:
            metadata = json.loads(conversation.metadata_json or "{}")
        except (TypeError, ValueError):
            continue
        plan = metadata.get("content_plan")
        if isinstance(plan, dict) and plan:
            return plan
    return None


def _build_approval_edit_prompt(workflow_type: str) -> str:
    mapping = {
        "kachu_google_post": "請直接回覆你想調整的方向，例如：語氣更像老闆本人、改成母親節檔期、結尾加上 CTA。",
        "kachu_photo_content": "請直接回覆你想調整的方向，例如：IG 版更活潑、Google 版更像公告、把重點改成新品。",
        "kachu_review_reply": "請直接回覆你想調整的方向，例如：更誠懇一點、縮短到 60 字內、加上補救承諾。",
        "kachu_planned_content": "請直接回覆你想調整的方向，例如：語氣更像老闆本人、主打新品、Google 版更像公告。",
        "kachu_meta_reply": "請直接回覆你想調整的方向，例如：更有溫度、縮短一點、改成先致歉再邀請私訊。",
    }
    return mapping.get(workflow_type, "請直接回覆你想怎麼修改，我會先重寫草稿再推回來給你確認。")


def _build_approval_edit_summary(workflow_type: str) -> str:
    mapping = {
        "kachu_google_post": "我已依照你的指示重寫 Google 商家動態草稿，請再確認一次。",
        "kachu_photo_content": "我已依照你的指示重寫貼文草稿，請再確認一次。",
        "kachu_review_reply": "我已依照你的指示重寫評論回覆草稿，請再確認一次。",
        "kachu_planned_content": "我已依照你的指示重寫企劃排程草稿，請再確認一次。",
        "kachu_meta_reply": "我已依照你的指示重寫外部互動回覆草稿，請再確認一次。",
    }
    return mapping.get(workflow_type, "我已依照你的指示重寫草稿，請再確認一次。")


def _build_pending_approval_flex(*, pending: Any, drafts: dict[str, Any]) -> dict[str, Any] | None:
    if pending.workflow_type == "kachu_review_reply":
        reply_payload = drafts.get("reply_draft", "")
        reply_text = reply_payload.get("reply_draft", "") if isinstance(reply_payload, dict) else str(reply_payload)
        return build_review_reply_flex(
            run_id=pending.agentos_run_id,
            tenant_id=pending.tenant_id,
            review_content=str(drafts.get("review_content", "")),
            reply_draft=reply_text,
        )
    if pending.workflow_type == "kachu_google_post":
        return build_google_post_flex(
            run_id=pending.agentos_run_id,
            tenant_id=pending.tenant_id,
            post_text=str(drafts.get("post_text") or drafts.get("google") or ""),
        )
    if pending.workflow_type == "kachu_photo_content":
        return build_photo_content_flex(
            run_id=pending.agentos_run_id,
            tenant_id=pending.tenant_id,
            drafts=drafts,
        )
    if pending.workflow_type == "kachu_planned_content":
        return build_planned_content_flex(
            run_id=pending.agentos_run_id,
            tenant_id=pending.tenant_id,
            drafts=drafts,
        )
    if pending.workflow_type == "kachu_meta_reply":
        return build_external_reply_flex(
            run_id=pending.agentos_run_id,
            tenant_id=pending.tenant_id,
            source_label=str(drafts.get("source_label") or "Meta 留言"),
            customer_name=str(drafts.get("author_name") or "顧客"),
            incoming_text=str(drafts.get("incoming_text") or ""),
            reply_draft=str(drafts.get("reply_draft") or ""),
        )
    return None


def _build_pending_approval_follow_up_messages(*, artifact: str, pending: Any) -> list[dict[str, Any]]:
    try:
        drafts = json.loads(pending.draft_content or "{}")
    except (TypeError, json.JSONDecodeError):
        drafts = {}

    if pending.status == "delivery_failed":
        lead_text = f"上一個{artifact}其實已經整理好了，只是剛剛通知沒有成功送達。我現在把草稿再貼給你確認。"
    else:
        lead_text = f"上一個{artifact}已經整理好了，我把草稿再貼給你確認一次。"

    messages: list[dict[str, Any]] = [text_message(lead_text)]
    flex_content = _build_pending_approval_flex(pending=pending, drafts=drafts)
    if flex_content is not None:
        messages.append({"type": "flex", "altText": "待確認草稿", "contents": flex_content})
        return messages

    fallback_summary = str(
        drafts.get("post_text")
        or drafts.get("ig_fb")
        or drafts.get("google")
        or drafts.get("reply_draft")
        or ""
    ).strip()
    if fallback_summary:
        messages.append(text_message(f"目前草稿重點如下：\n{fallback_summary[:1000]}"))
    return messages


def _is_pending_asset_intent_active(pending_asset: Any | None) -> bool:
    if pending_asset is None or str(getattr(pending_asset, "status", "") or "") != "pending":
        return False
    expires_at = getattr(pending_asset, "expires_at", None)
    if expires_at is None:
        return True
    # SQLite 回傳的是 naive datetime；統一當成 UTC 比較
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _parse_pending_asset_payload(pending_asset: Any) -> dict[str, Any]:
    try:
        payload = json.loads(getattr(pending_asset, "payload_json", "{}") or "{}")
    except (TypeError, json.JSONDecodeError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _resolve_pending_asset_decision_from_text(text: str) -> str:
    normalized = "".join(str(text or "").split())
    if not normalized:
        return ""
    if any(token in normalized for token in ("先討論", "想討論", "討論一下", "聊聊", "先不要發")):
        return "consult"
    if any(token in normalized for token in ("進知識庫", "知識庫", "記住這張", "收進品牌資料", "吸收這張")):
        return "knowledge_update"
    if any(token in normalized for token in ("寫貼文", "發貼文", "用這張圖寫", "拿來發文", "幫我發", "貼文")):
        return "photo_content"
    return ""


def _build_asset_knowledge_entry_content(payload: dict[str, Any]) -> str:
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    scene = str(analysis.get("scene_description") or "老闆上傳了一張品牌素材圖片。")
    upload_intent = str(analysis.get("upload_intent") or "")
    detected_objects = analysis.get("detected_objects") if isinstance(analysis.get("detected_objects"), list) else []
    segments = [f"品牌素材圖片：{scene}"]
    if upload_intent:
        segments.append(f"建議用途：{upload_intent}")
    cleaned_objects = [str(item).strip() for item in detected_objects if str(item).strip()]
    if cleaned_objects:
        segments.append("畫面元素：" + "、".join(cleaned_objects[:6]))
    line_message_id = str(payload.get("line_message_id") or "").strip()
    if line_message_id:
        segments.append(f"來源：LINE 圖片 {line_message_id}")
    return "\n".join(segments)


async def _process_pending_asset_intent(
    *,
    app: Any,
    repo: Any,
    tenant_id: str,
    line_user_id: str,
    pending_asset: Any,
    decision: str,
    execute_dispatcher: AgentOSTaskDispatcher,
    consultant: LLMConsultant,
) -> tuple[list[dict[str, Any]], str, str, str]:
    payload = _parse_pending_asset_payload(pending_asset)
    analysis = payload.get("analysis") if isinstance(payload.get("analysis"), dict) else {}
    line_message_id = str(payload.get("line_message_id") or "").strip()
    photo_url = str(payload.get("photo_url") or "").strip()
    source_conversation_id = str(payload.get("source_conversation_id") or "").strip()

    if decision == "photo_content":
        task = await execute_dispatcher.dispatch(
            tenant_id=tenant_id,
            text=f"photo:{line_message_id or pending_asset.id}",
            intent_label="photo_content",
            workflow_input_patch={
                "tenant_id": tenant_id,
                "line_message_id": line_message_id,
                "photo_url": photo_url,
                "analysis": analysis,
                "trigger_source": "boss_photo_asset_choice",
            },
        )
        repo.save_execute_task_record(
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            intent_label="photo_content",
            source_text=f"photo:{line_message_id or pending_asset.id}",
            objective=task.objective,
            task_id=task.task_id,
            run_id=task.current_run_id or "",
            related_conversation_id=source_conversation_id,
            status=task.status,
        )
        repo.resolve_pending_asset_intent(
            intent_id=pending_asset.id,
            status="resolved",
            selected_decision=decision,
        )
        return [text_message(_build_execute_ack(intent_label="photo_content", task=task))], "execute_ack", task.task_id, task.current_run_id or ""

    if decision == "knowledge_update":
        repo.save_knowledge_entry(
            tenant_id=tenant_id,
            category="brand_material",
            content=_build_asset_knowledge_entry_content(payload),
            source_type="image_upload",
            source_conversation_id=source_conversation_id,
        )
        repo.resolve_pending_asset_intent(
            intent_id=pending_asset.id,
            status="resolved",
            selected_decision=decision,
        )
        scene = str(analysis.get("scene_description") or "這張圖")
        return [
            text_message(
                f"好，我先把這張圖收進品牌知識庫了。\n我會把「{scene}」當成品牌素材一起參考。\n如果你要，我下一步也可以直接用它寫貼文或幫你整理重點。"
            )
        ], "execute_result", "", ""

    if decision == "consult":
        tenant = repo.get_tenant(tenant_id)
        scene = str(analysis.get("scene_description") or "這張照片")
        upload_intent = str(analysis.get("upload_intent") or "")
        context_bundle = await _get_consult_context_builder(app, repo).build_bundle(
            tenant_id=tenant_id,
            message=f"先討論這張照片怎麼用：{scene}",
            line_user_id=line_user_id,
        )
        prompt = (
            f"老闆剛上傳一張照片，畫面內容是：{scene}。"
            + (f" 系統目前推測用途偏向：{upload_intent}。" if upload_intent else "")
            + " 老闆想先討論這張圖適合怎麼用。"
            "請用繁體中文先給 2 到 3 個可行方向，語氣像商業夥伴，最後補一句追問。"
        )
        reply = await consultant.build_reply(
            tenant_name=getattr(tenant, "name", "") if tenant is not None else "",
            industry_type=getattr(tenant, "industry_type", "") if tenant is not None else "",
            message=prompt,
            context_bundle=context_bundle,
        )
        repo.resolve_pending_asset_intent(
            intent_id=pending_asset.id,
            status="resolved",
            selected_decision=decision,
        )
        return [text_message(reply)], "boss_consult", "", ""

    raise ValueError(f"unsupported asset intent decision: {decision}")


async def _revise_pending_approval_draft(
    *,
    repo: Any,
    consultant: LLMConsultant,
    tenant_id: str,
    pending: Any,
    instruction: str,
) -> dict[str, Any]:
    drafts = json.loads(pending.draft_content or "{}")
    tenant = repo.get_tenant(tenant_id)
    tenant_name = getattr(tenant, "name", "") or "你的店"
    industry_type = getattr(tenant, "industry_type", "") or "一般服務業"

    if pending.workflow_type == "kachu_review_reply":
        original = str(drafts.get("reply_draft") or "")
        review_content = str(drafts.get("review_content") or "")
        revised = await consultant.build_reply(
            tenant_name=tenant_name,
            industry_type="review_reply",
            message=(
                f"請根據老闆指示重寫評論回覆。\n"
                f"原草稿：{original}\n"
                f"評論內容：{review_content}\n"
                f"修改指示：{instruction}\n"
                "請輸出最終回覆文字，不要加說明。"
            ),
        )
        return {**drafts, "reply_draft": revised.strip() or original}

    if pending.workflow_type == "kachu_google_post":
        original = str(drafts.get("post_text") or drafts.get("google") or "")
        revised = await consultant.build_reply(
            tenant_name=tenant_name,
            industry_type="google_post",
            message=(
                f"請根據老闆指示重寫 Google 商家動態。\n"
                f"原草稿：{original}\n"
                f"修改指示：{instruction}\n"
                "請輸出最終貼文文字，不要加說明。"
            ),
        )
        final_text = revised.strip() or original
        return {**drafts, "post_text": final_text, "google": final_text}

    if pending.workflow_type in {"kachu_photo_content", "kachu_planned_content"}:
        original_ig = str(drafts.get("ig_fb") or "")
        original_google = str(drafts.get("google") or "")
        revised_ig = await consultant.build_reply(
            tenant_name=tenant_name,
            industry_type="photo_content",
            message=(
                f"請根據老闆指示重寫 IG/FB 貼文。\n"
                f"原草稿：{original_ig}\n"
                f"修改指示：{instruction}\n"
                "請輸出最終貼文文字，不要加說明。"
            ),
        )
        revised_google = await consultant.build_reply(
            tenant_name=tenant_name,
            industry_type="photo_content",
            message=(
                f"請根據老闆指示重寫 Google 商家動態。\n"
                f"原草稿：{original_google}\n"
                f"修改指示：{instruction}\n"
                "請輸出最終貼文文字，不要加說明。"
            ),
        )
        return {
            **drafts,
            "ig_fb": revised_ig.strip() or original_ig,
            "google": revised_google.strip() or original_google,
        }

    if pending.workflow_type == "kachu_meta_reply":
        original = str(drafts.get("reply_draft") or "")
        incoming = str(drafts.get("incoming_text") or "")
        revised = await consultant.build_reply(
            tenant_name=tenant_name,
            industry_type="meta_reply",
            message=(
                f"請根據老闆指示重寫 Meta 回覆。\n"
                f"原草稿：{original}\n"
                f"對方訊息：{incoming}\n"
                f"修改指示：{instruction}\n"
                "請輸出最終回覆文字，不要加說明。"
            ),
        )
        return {**drafts, "reply_draft": revised.strip() or original}

    raise ValueError(f"unsupported approval edit workflow: {pending.workflow_type}")


def _is_local_pending_workflow(pending: Any | None) -> bool:
    return bool(pending is not None and pending.workflow_type in {"kachu_planned_content", "kachu_meta_reply"})


def _is_active_editing_pending(pending: Any | None) -> bool:
    if pending is None:
        return False
    return str(getattr(pending, "status", "") or "").strip() == "editing"


async def _handle_local_pending_approval(
    *,
    app: Any,
    repo: Any,
    tenant_id: str,
    pending: Any,
    action: ApprovalAction,
    actor_line_id: str,
) -> str:
    run_id = pending.agentos_run_id
    drafts = json.loads(pending.draft_content or "{}")
    if action == ApprovalAction.REJECT:
        repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision="rejected",
            actor_line_id=actor_line_id or "owner",
            decision_payload={"reason": "rejected_from_line"},
        )
        if pending.workflow_type == "kachu_planned_content" and drafts.get("content_plan_item_id"):
            repo.update_content_plan_item(item_id=str(drafts["content_plan_item_id"]), status="rejected")
        if pending.workflow_type == "kachu_meta_reply" and drafts.get("engagement_id"):
            repo.update_external_engagement(engagement_id=str(drafts["engagement_id"]), status="rejected")
        return "已先取消這則草稿。"

    if pending.workflow_type == "kachu_planned_content":
        results = publish_content_bundle(
            repo=repo,
            review_service=getattr(app.state, "google_review_service", None) or _review_service_like(app, repo),
            tenant_id=tenant_id,
            run_id=run_id,
            drafts=drafts,
            selected_platforms=drafts.get("selected_platforms"),
            workflow_type="kachu_planned_content",
        )
        delivery_succeeded = publish_content_bundle_succeeded(results)
        repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision="published" if delivery_succeeded else "delivery_failed",
            actor_line_id=actor_line_id or "owner",
            decision_payload={"results": results},
        )
        if drafts.get("content_plan_item_id"):
            repo.update_content_plan_item(
                item_id=str(drafts["content_plan_item_id"]),
                status="published" if delivery_succeeded else "delivery_failed",
                draft_payload=drafts,
                pending_run_id=run_id,
            )
        return "企劃排程草稿已發布完成。" if delivery_succeeded else "草稿尚未發布，請先檢查 Google / Meta 連接憑證是否有效。"

    if pending.workflow_type == "kachu_meta_reply":
        engagement = repo.get_external_engagement(str(drafts.get("engagement_id") or ""))
        if engagement is None:
            raise HTTPException(status_code=404, detail="external engagement not found")
        result = publish_meta_reply(
            repo=repo,
            tenant_id=tenant_id,
            run_id=run_id,
            engagement=engagement,
            reply_text=str(drafts.get("reply_draft") or ""),
        )
        delivery_succeeded = str(result.get("status", "") or "") == "posted"
        repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision="published" if delivery_succeeded else "delivery_failed",
            actor_line_id=actor_line_id or "owner",
            decision_payload=result,
        )
        repo.update_external_engagement(
            engagement_id=engagement.id,
            status="replied" if delivery_succeeded else "delivery_failed",
            reply_draft=str(drafts.get("reply_draft") or ""),
            related_run_id=run_id,
        )
        return "Meta 回覆已送出。" if delivery_succeeded else "Meta 回覆未送出，請先檢查粉專連接或憑證是否過期。"

    raise HTTPException(status_code=400, detail="unsupported local approval workflow")


def _review_service_like(app: Any, repo: Any) -> Any:
    service = getattr(app.state, "google_review_service", None)
    if service is None:
        from kachu_plus.google_business import GoogleReviewService

        service = GoogleReviewService(repo, app.state.settings)
        app.state.google_review_service = service
    return service


async def _refresh_execute_task_reply(
    *,
    tenant_id: str,
    line_user_id: str,
    repo: Any,
    execute_dispatcher: AgentOSTaskDispatcher,
) -> list[dict[str, Any]]:
    record = repo.get_latest_execute_task_record(tenant_id=tenant_id, line_user_id=line_user_id)
    if record is None:
        return [text_message("目前找不到你最近的草稿任務。你可以直接再說一次「幫我寫一篇貼文」或「幫我回覆評論」。")]

    artifact = _describe_execute_artifact(record.intent_label)
    task_view = await execute_dispatcher.get_task(record.task_id)
    task_status = str(task_view.task.get("status", record.status) or record.status)
    run_id = str(task_view.task.get("current_run_id", "") or record.run_id or "")
    repo.update_execute_task_record(task_id=record.task_id, run_id=run_id, status=task_status)

    if not run_id:
        if task_status == "created":
            return [text_message(f"上一個{artifact}任務已建立，但還沒開始執行。請直接再說一次原需求，我會重新幫你起草。")]
        return [text_message(f"上一個{artifact}目前狀態是 {task_status}，我還沒拿到可回推的內容。")]

    run_view = await execute_dispatcher.get_run(run_id)
    run_status = str(run_view.run.get("status", task_status) or task_status)
    repo.update_execute_task_record(task_id=record.task_id, run_id=run_id, status=run_status)

    pending = repo.get_pending_approval_by_run_id(run_id)
    if pending is not None and pending.status in {"pending", "delivery_failed"}:
        return _build_pending_approval_follow_up_messages(artifact=artifact, pending=pending)

    if run_status in {"created", "queued", "running", "in_progress"}:
        return [text_message(f"我還在整理上一個{artifact}，完成後會推播給你確認。")] 
    if run_status == "waiting_approval":
        return [text_message(f"上一個{artifact}已經整理好，正在等你確認。")] 
    if run_status in {"completed", "succeeded"}:
        return [text_message(f"上一個{artifact}已完成；如果你還沒收到 LINE 草稿，代表結果沒有成功回推，請直接再說一次原需求。")] 
    if run_status in {"failed", "error", "cancelled"}:
        return [text_message(f"上一個{artifact}沒有成功完成。請直接再說一次原需求，我會重新幫你起草。")] 
    return [text_message(f"上一個{artifact}目前狀態是 {run_status}。")]


def _verify_line_signature(body: bytes, channel_secret: str, signature: str) -> bool:
    """
    LINE webhook 簽章驗證（渠道 R3）。
    HMAC-SHA256(body, channel_secret) → base64，與 X-Line-Signature header 比對。
    使用 hmac.compare_digest 防止 timing attack。
    """
    digest = hmac.new(
        channel_secret.encode("utf-8"),
        body,
        hashlib.sha256,
    ).digest()
    expected = base64.b64encode(digest).decode("utf-8")
    return hmac.compare_digest(expected, signature)


async def _handle_event(
    *,
    app: Any,
    event: dict[str, Any],
    tenant_id: str,
    repo: Any,
    onboarding_flow: OnboardingFlow,
    execute_dispatcher: AgentOSTaskDispatcher,
    consultant: LLMConsultant,
    sleep_query_service: SleepCustomerQueryService,
    policy_resolver: Any = None,
    channel_access_token: str = "",
) -> None:
    """
    處理單一 LINE event。
    - Onboarding 中的 tenant → OnboardingFlow，並 push 回覆到 LINE
    - 完成 onboarding → IntentRouter (EXECUTE / CONSULT / CLARIFY)，並 push 回覆
    """
    source = event.get("source", {})
    line_user_id = source.get("userId", "")
    event_type = event.get("type", "")

    # 只處理 message / follow 事件
    if event_type not in ("message", "follow"):
        logger.info(
            "tenant=%s event_type=%s user=%s — skipped (not message/follow)",
            tenant_id, event_type, line_user_id,
        )
        return

    active_membership = None
    if line_user_id:
        memberships = repo.list_active_memberships(tenant_id)
        active_membership = next((item for item in memberships if item.line_user_id == line_user_id), None)
        try:
            if active_membership is None:
                active_membership = repo.create_tenant_membership(
                    tenant_id=tenant_id,
                    line_user_id=line_user_id,
                    role="owner",
                )
        except ValueError:
            logger.info("tenant=%s line_user=%s membership bind skipped due to active conflict", tenant_id, line_user_id)
        profile = repo.resolve_or_create_line_profile(tenant_id, line_user_id)
        logger.info(
            "tenant=%s line_user=%s profile_id=%s interaction_count=%s",
            tenant_id,
            line_user_id,
            profile.id,
            profile.interaction_count,
        )

    if event_type == "follow":
        # A-2：新加好友 → 觸發 onboarding 歡迎訊息並實際推播
        if onboarding_flow.is_in_onboarding(tenant_id):
            replies = await onboarding_flow.handle_message(tenant_id, "text", "")
            for r in replies:
                logger.info("tenant=%s [onboarding follow reply] %s", tenant_id, r.get("text", "")[:80])
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="onboarding",
                messages=replies,
                access_token=channel_access_token,
            )
        return

    message = event.get("message", {})
    msg_type = message.get("type", "")
    text = message.get("text", "") if msg_type == "text" else ""
    line_message_id = str(message.get("id", "") or "")

    logger.info(
        "LINE event: tenant=%s type=%s user=%s text=%s",
        tenant_id, msg_type, line_user_id, text[:80],
    )

    # ── Onboarding path ───────────────────────────────────────────────────────
    if onboarding_flow.is_in_onboarding(tenant_id):
        source_conversation = _record_inbound_conversation(
            app=app,
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="boss",
            conversation_kind="onboarding",
            msg_type=msg_type,
            text=text,
            line_message_id=line_message_id,
        )
        replies = await onboarding_flow.handle_message(
            tenant_id,
            msg_type,
            text,
            source_conversation_id=getattr(source_conversation, "id", ""),
        )
        for r in replies:
            logger.info("tenant=%s [onboarding reply] %s", tenant_id, r.get("text", "")[:80])
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind="onboarding",
            messages=replies,
            access_token=channel_access_token,
        )
        return

    if active_membership is not None and active_membership.role == "customer":
        if msg_type != "text" or not text.strip():
            logger.info("tenant=%s customer non-text message ignored user=%s", tenant_id, line_user_id)
            return
        handoff_lock = repo.get_active_conversation_handoff_lock(
            tenant_id=tenant_id,
            channel_type="line",
            external_user_id=line_user_id,
        )
        _record_inbound_conversation(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="customer",
            conversation_kind="customer_faq",
            msg_type=msg_type,
            text=text,
            line_message_id=line_message_id,
            metadata={
                "handoff_locked": handoff_lock is not None,
                "handoff_reason": str(getattr(handoff_lock, "reason", "") or ""),
            },
        )
        if handoff_lock is not None:
            logger.info(
                "tenant=%s [CUSTOMER_FAQ_LOCKED] user=%s reason=%s",
                tenant_id,
                line_user_id,
                str(handoff_lock.reason or ""),
            )
            return
        faq_result = await run_line_faq_flow(
            repo=repo,
            settings=app.state.settings,
            tenant_id=tenant_id,
            customer_line_id=line_user_id,
            message=text,
            access_token_override=channel_access_token,
        )
        logger.info(
            "tenant=%s [CUSTOMER_FAQ] action=%s user=%s",
            tenant_id,
            faq_result.get("action", "unknown"),
            line_user_id,
        )
        return

    if msg_type == "image" and line_message_id and channel_access_token:
        source_conversation = _record_inbound_conversation(
            app=app,
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="boss",
            conversation_kind="boss_command",
            msg_type=msg_type,
            text=text,
            line_message_id=line_message_id,
            metadata={"line_message_id": line_message_id},
        )
        try:
            content_bytes = await _download_line_message_content(line_message_id, channel_access_token)
            photo_url = "data:image/jpeg;base64," + base64.b64encode(content_bytes).decode("ascii")
            analysis = await analyze_photo_payload(
                photo_url=photo_url,
                line_message_id=line_message_id,
                settings=app.state.settings,
            )
            pending_asset = repo.save_pending_asset_intent(
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                line_message_id=line_message_id,
                payload={
                    "line_message_id": line_message_id,
                    "photo_url": photo_url,
                    "analysis": analysis,
                    "source_conversation_id": getattr(source_conversation, "id", ""),
                },
                expires_at=datetime.now(timezone.utc) + _PENDING_ASSET_INTENT_TTL,
            )
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="follow_up",
                messages=[
                    build_asset_intent_prompt_message(
                        asset_intent_id=pending_asset.id,
                        tenant_id=tenant_id,
                        analysis=analysis,
                    )
                ],
                access_token=channel_access_token,
            )
        except httpx.HTTPError:
            logger.exception("tenant=%s [EXECUTE] LINE image download failed", tenant_id)
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message("照片下載失敗，請重新上傳後再試一次。")],
                access_token=channel_access_token,
            )
        except UnsupportedExecutionIntentError as exc:
            logger.info("tenant=%s [EXECUTE_UNSUPPORTED] intent=photo_content reason=%s", tenant_id, str(exc))
        return

    # ── Intent router path（1-5：三種回應路徑）────────────────────────────────
    if msg_type != "text" or not text.strip():
        logger.info("tenant=%s non-text message, no intent routing", tenant_id)
        return

    pending_asset = repo.get_latest_pending_asset_intent(
        tenant_id=tenant_id,
        line_user_id=line_user_id,
    )
    pending_asset_decision = _resolve_pending_asset_decision_from_text(text) if _is_pending_asset_intent_active(pending_asset) else ""
    if pending_asset is not None and pending_asset_decision:
        _record_inbound_conversation(
            app=app,
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="boss",
            conversation_kind="follow_up",
            msg_type=msg_type,
            text=text,
            line_message_id=line_message_id,
            metadata={
                "asset_intent_id": pending_asset.id,
                "asset_decision": pending_asset_decision,
            },
        )
        try:
            reply_messages, conversation_kind, related_task_id, related_run_id = await _process_pending_asset_intent(
                app=app,
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                pending_asset=pending_asset,
                decision=pending_asset_decision,
                execute_dispatcher=execute_dispatcher,
                consultant=consultant,
            )
        except UnsupportedExecutionIntentError:
            logger.exception("tenant=%s [ASSET_INTENT] unsupported execute route", tenant_id)
            reply_messages = [text_message("這張圖目前還不能直接走這條流程，你可以改成先討論或換張圖。")]
            conversation_kind = "execute_result"
            related_task_id = ""
            related_run_id = ""
        except httpx.HTTPError:
            logger.exception("tenant=%s [ASSET_INTENT] downstream HTTP error", tenant_id)
            reply_messages = [text_message("系統暫時無法接手這張圖，請稍後再試一次。")]
            conversation_kind = "execute_result"
            related_task_id = ""
            related_run_id = ""
        logger.info(
            "tenant=%s [ASSET_INTENT] decision=%s",
            tenant_id,
            pending_asset_decision,
        )
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind=conversation_kind,
            messages=reply_messages,
            access_token=channel_access_token,
            related_task_id=related_task_id,
            related_run_id=related_run_id,
        )
        return

    latest_content_plan = _get_latest_content_plan(
        repo,
        tenant_id=tenant_id,
        line_user_id=line_user_id,
    )
    if latest_content_plan is not None and _looks_like_plan_to_draft_request(text):
        source_conversation = _record_inbound_conversation(
            app=app,
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="boss",
            conversation_kind="follow_up",
            msg_type=msg_type,
            text=text,
            line_message_id=line_message_id,
            metadata={"apply_content_plan": True},
        )
        tenant = repo.get_tenant(tenant_id)
        context = {
            "brand_name": getattr(tenant, "name", "") or "你的店",
            "brand_tone": latest_content_plan.get("brand_tone", "親切真誠"),
            "content_plan": latest_content_plan,
        }
        drafts = await build_content_drafts(
            consultant=consultant,
            context=context,
            analysis={},
            content_plan=latest_content_plan,
        )
        run_id = f"content-plan-inline:{getattr(source_conversation, 'id', 'draft')}"
        repo.save_pending_approval(
            tenant_id=tenant_id,
            agentos_task_id=getattr(source_conversation, "id", ""),
            agentos_run_id=run_id,
            workflow_type="kachu_planned_content",
            draft_content=json.dumps(drafts, ensure_ascii=False),
        )
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind="execute_result",
            messages=[
                text_message("我已根據剛剛的企劃先起草兩個版本，你可以直接拿去發布，或再叫我修改。"),
                {
                    "type": "flex",
                    "altText": "企劃延伸草稿",
                    "contents": build_planned_content_flex(
                        run_id=run_id,
                        tenant_id=tenant_id,
                        drafts=drafts,
                    ),
                },
            ],
            access_token=channel_access_token,
        )
        return

    editing_pending = repo.get_latest_editing_approval(
        tenant_id=tenant_id,
        actor_line_id=line_user_id,
    )
    if _is_active_editing_pending(editing_pending):
        _record_inbound_conversation(
            app=app,
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            actor_role="boss",
            conversation_kind="approval_edit",
            msg_type=msg_type,
            text=text,
            line_message_id=line_message_id,
            metadata={
                "run_id": editing_pending.agentos_run_id,
                "workflow_type": editing_pending.workflow_type,
                "edit_instruction": text,
            },
        )
        try:
            revised_drafts = await _revise_pending_approval_draft(
                repo=repo,
                consultant=consultant,
                tenant_id=tenant_id,
                pending=editing_pending,
                instruction=text,
            )
        except ValueError:
            logger.info(
                "tenant=%s [APPROVAL_EDIT_UNSUPPORTED] workflow=%s",
                tenant_id,
                editing_pending.workflow_type,
            )
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="approval_edit",
                messages=[text_message("這種草稿目前還不能直接在 LINE 內改稿，請改用後台編輯。")],
                access_token=channel_access_token,
                related_run_id=editing_pending.agentos_run_id,
            )
            return

        repo.update_pending_approval_draft(
            agentos_run_id=editing_pending.agentos_run_id,
            draft_content=json.dumps(revised_drafts, ensure_ascii=False),
            actor_line_id=line_user_id,
            status="pending",
        )
        approval_messages: list[dict[str, Any]] = [text_message(_build_approval_edit_summary(editing_pending.workflow_type))]
        flex_content = _build_pending_approval_flex(pending=editing_pending, drafts=revised_drafts)
        if flex_content is not None:
            approval_messages.append({"type": "flex", "altText": "更新後草稿", "contents": flex_content})
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind="approval_edit",
            messages=approval_messages,
            access_token=channel_access_token,
            related_run_id=editing_pending.agentos_run_id,
        )
        return

    dialogue_state = _get_dialogue_state_resolver(app, repo).resolve(
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        text=text,
    )
    decision = dialogue_state.apply(classify_boss_message(text))
    conversation_kind = "boss_command"
    if decision.mode == BossRouteMode.CONSULT:
        conversation_kind = "boss_consult"
    elif decision.mode == BossRouteMode.CLARIFY:
        conversation_kind = "follow_up"
    source_conversation = _record_inbound_conversation(
        app=app,
        repo=repo,
        tenant_id=tenant_id,
        line_user_id=line_user_id,
        actor_role="boss",
        conversation_kind=conversation_kind,
        msg_type=msg_type,
        text=text,
        line_message_id=line_message_id,
        metadata={
            "intent_label": decision.intent_label,
            "mode": decision.mode.value,
            "state_reason": dialogue_state.reason,
            "is_follow_up": dialogue_state.is_follow_up,
            "carry_over_task_id": dialogue_state.carry_over_task_id,
            "carry_over_run_id": dialogue_state.carry_over_run_id,
        },
    )
    if decision.mode == BossRouteMode.EXECUTE:
        if decision.intent_label == "content_plan":
            tenant = repo.get_tenant(tenant_id)
            industry_type = getattr(tenant, "industry_type", "") or "一般服務業"
            context_bundle = await _get_consult_context_builder(app, repo).build_bundle(
                tenant_id=tenant_id,
                message=text,
                line_user_id=line_user_id,
            )
            plan = await build_content_plan_payload(
                consultant=consultant,
                tenant_name=getattr(tenant, "name", "") or "你的店",
                industry_type=industry_type,
                objective=text,
                selected_platforms=["ig_fb", "google"],
                context={
                    "brand_name": getattr(tenant, "name", "") or "你的店",
                    "brand_tone": (context_bundle.get("brand_brief") or {}).get("brand_tone", "親切真誠"),
                    "brand_brief": context_bundle.get("brand_brief") or {},
                    "owner_brief": context_bundle.get("owner_brief") or {},
                    "knowledge": context_bundle.get("relevant_knowledge") or [],
                    "industry_context": build_industry_context(industry_type),
                },
            )
            reply = _format_content_plan_reply(plan)
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="content_plan",
                messages=[text_message(reply)],
                access_token=channel_access_token,
                metadata={"content_plan": plan},
            )
            return
        if decision.intent_label == "website_ingest":
            repo.save_knowledge_entry(
                tenant_id=tenant_id,
                category="brand_material",
                content=text.strip(),
                source_conversation_id=getattr(source_conversation, "id", ""),
            )
            website_ingestion_service = WebsiteKnowledgeIngestionService(repo)
            try:
                result = await website_ingestion_service.ingest_from_message(
                    tenant_id=tenant_id,
                    text=text,
                    source_conversation_id=getattr(source_conversation, "id", ""),
                )
            except httpx.HTTPError:
                logger.exception("tenant=%s [WEBSITE_INGEST] fetch failed", tenant_id)
                reply = "我有收到網址，但這次抓網站內容失敗了。你可以稍後再貼一次，或直接補充重點給我。"
            except Exception:
                logger.exception("tenant=%s [WEBSITE_INGEST] unexpected failure", tenant_id)
                reply = "我有收到網址，但整理網站內容時出現問題。你可以稍後再貼一次，或先直接補充品牌重點給我。"
            else:
                reply = (
                    format_website_ingestion_reply(result)
                    if result is not None
                    else "我有收到網址，但這次沒有抓到可用的網站內容。你可以直接再貼一次，或補充品牌重點給我。"
                )
            logger.info(
                "tenant=%s [EXECUTE] intent=%s reply=%s",
                tenant_id,
                decision.intent_label,
                reply[:160],
            )
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
            return
        if decision.intent_label == "draft_status":
            reply_messages = await _refresh_execute_task_reply(
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                repo=repo,
                execute_dispatcher=execute_dispatcher,
            )
            reply_texts = _extract_text_messages(reply_messages)
            logger.info(
                "tenant=%s [EXECUTE] intent=%s reply=%s",
                tenant_id,
                decision.intent_label,
                (reply_texts[0] if reply_texts else "(non-text reply)")[:160],
            )
            latest_record = repo.get_latest_execute_task_record(tenant_id=tenant_id, line_user_id=line_user_id)
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="follow_up",
                messages=reply_messages,
                access_token=channel_access_token,
                related_task_id=getattr(latest_record, "task_id", "") if latest_record is not None else "",
                related_run_id=getattr(latest_record, "run_id", "") if latest_record is not None else "",
            )
            return
        if decision.intent_label == "sleep_customer_query":
            reply = sleep_query_service.build_reply(tenant_id=tenant_id, text=text)
            logger.info(
                "tenant=%s [EXECUTE] intent=%s reply=%s",
                tenant_id,
                decision.intent_label,
                reply[:120],
            )
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
            return
        if decision.intent_label == "meta_insights":
            try:
                insights = _get_meta_insights_service(app, repo).fetch_insights(
                    tenant_id=tenant_id,
                    period="week",
                )
                tenant = repo.get_tenant(tenant_id)
                summary_payload = await summarize_meta_insights(
                    tenant_name=getattr(tenant, "name", ""),
                    industry_type=getattr(tenant, "industry_type", ""),
                    insights=insights,
                    consultant=consultant,
                )
                reply = summary_payload["summary"]
                recipients = [line_user_id] if line_user_id else resolve_tenant_line_recipients(
                    repo=repo,
                    settings=app.state.settings,
                    tenant_id=tenant_id,
                )
                await deliver_meta_insights_report(
                    repo=repo,
                    settings=app.state.settings,
                    tenant_id=tenant_id,
                    summary=reply,
                    details=summary_payload["details"],
                    period=str(insights.get("period", "week") or "week"),
                    recipient_line_ids=recipients,
                )
                logger.info(
                    "tenant=%s [EXECUTE] intent=%s reply=%s",
                    tenant_id,
                    decision.intent_label,
                    reply[:120],
                )
            except MetaConnectorError as exc:
                logger.info(
                    "tenant=%s [EXECUTE_META_ERROR] intent=%s reason=%s",
                    tenant_id,
                    decision.intent_label,
                    str(exc),
                )
                await _push_and_record_texts(
                    repo=repo,
                    tenant_id=tenant_id,
                    line_user_id=line_user_id,
                    conversation_kind="execute_result",
                    messages=[text_message("Meta 尚未連接，請先完成 Meta（FB/IG）授權。")],
                    access_token=channel_access_token,
                )
            return
        if decision.intent_label in {"meta_connect", "meta_reauth"}:
            manage_url = build_meta_manage_url(settings=app.state.settings, tenant_id=tenant_id, line_user_id=line_user_id)
            status_payload = _get_meta_oauth_service(app, repo).get_connection_status(tenant_id=tenant_id)
            connector = status_payload.get("connector") or {}
            current_page = str(connector.get("account_label", "") or connector.get("fb_page_id", "") or "")
            prefix = "你目前已連接粉專「%s」。" % current_page if current_page else "目前尚未連接 Meta。"
            reply = f"{prefix} 請點這個連結完成 Meta 授權與管理：{manage_url}"
            logger.info("tenant=%s [EXECUTE] intent=%s reply=%s", tenant_id, decision.intent_label, reply[:160])
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
            return
        if decision.intent_label == "meta_status":
            manage_url = build_meta_manage_url(settings=app.state.settings, tenant_id=tenant_id, line_user_id=line_user_id)
            status_payload = _get_meta_oauth_service(app, repo).get_connection_status(tenant_id=tenant_id)
            connector = status_payload.get("connector") or {}
            if status_payload.get("connected"):
                reply = (
                    f"你目前連接的是 Facebook 粉專「{connector.get('account_label') or connector.get('fb_page_id') or '未命名粉專'}」。"
                    + (" Instagram 也已連接。" if str(connector.get("ig_user_id", "") or "").strip() else " Instagram 尚未連接。")
                    + f" 管理連結：{manage_url}"
                )
            else:
                reply = f"你目前還沒有連接 Meta。可從這裡開始：{manage_url}"
            logger.info("tenant=%s [EXECUTE] intent=%s reply=%s", tenant_id, decision.intent_label, reply[:160])
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
            return
        if decision.intent_label == "meta_disconnect":
            result = _get_meta_oauth_service(app, repo).disconnect(tenant_id=tenant_id)
            if result.get("status") == "disconnected":
                reply = "已解除 Meta 連接。之後若要重新接通，直接說「我要重新授權 FB/IG」即可。"
            else:
                reply = "目前沒有可解除的 Meta 連接。"
            logger.info("tenant=%s [EXECUTE] intent=%s reply=%s", tenant_id, decision.intent_label, reply[:160])
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message(reply)],
                access_token=channel_access_token,
            )
            return
        if decision.intent_label == "tag_management":
            await _handle_tag_management(
                tenant_id=tenant_id,
                text=text,
                line_user_id=line_user_id,
                repo=repo,
                channel_access_token=channel_access_token,
            )
            return
        try:
            workflow_input_patch = None
            if policy_resolver is not None and decision.intent_label in {"review_reply", "google_post"}:
                workflow_input_patch = policy_resolver.resolve(tenant_id)
            if dialogue_state.is_follow_up and dialogue_state.carry_over_task_id:
                workflow_input_patch = {
                    **(workflow_input_patch or {}),
                    "follow_up_of_task_id": dialogue_state.carry_over_task_id,
                    "follow_up_of_run_id": dialogue_state.carry_over_run_id,
                    "follow_up_instruction": text,
                    "previous_source_text": dialogue_state.carry_over_source_text,
                }
            task = await execute_dispatcher.dispatch(
                tenant_id=tenant_id,
                text=text,
                intent_label=decision.intent_label,
                workflow_input_patch=workflow_input_patch,
            )
            logger.info(
                "tenant=%s [EXECUTE] intent=%s task_id=%s domain=%s status=%s",
                tenant_id,
                decision.intent_label,
                task.task_id,
                task.domain,
                task.status,
            )
            repo.save_execute_task_record(
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                intent_label=decision.intent_label,
                source_text=text,
                objective=task.objective,
                task_id=task.task_id,
                run_id=task.current_run_id or "",
                related_conversation_id=getattr(source_conversation, "id", ""),
                status=task.status,
            )
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_ack",
                messages=[text_message(_build_execute_ack(intent_label=decision.intent_label, task=task))],
                access_token=channel_access_token,
                related_task_id=task.task_id,
                related_run_id=task.current_run_id or "",
            )
        except UnsupportedExecutionIntentError as exc:
            logger.info(
                "tenant=%s [EXECUTE_UNSUPPORTED] intent=%s reason=%s",
                tenant_id,
                decision.intent_label,
                str(exc),
            )
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message("這個指令我目前還不支援，你可以換個方式說看看。")],
                access_token=channel_access_token,
            )
        except httpx.HTTPError:
            logger.exception("tenant=%s [EXECUTE] AgentOS dispatch failed", tenant_id)
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=line_user_id,
                conversation_kind="execute_result",
                messages=[text_message("系統暫時無法處理，請稍後再試。")],
                access_token=channel_access_token,
            )

    elif decision.mode == BossRouteMode.CONSULT:
        tenant = repo.get_tenant(tenant_id)
        context_bundle = await _get_consult_context_builder(app, repo).build_bundle(
            tenant_id=tenant_id,
            message=text,
            line_user_id=line_user_id,
        )
        reply = await consultant.build_reply(
            tenant_name=tenant.name if tenant is not None else "",
            industry_type=tenant.industry_type if tenant is not None else "",
            message=text,
            context_bundle=context_bundle,
        )
        logger.info(
            "tenant=%s [CONSULT] reply=%s",
            tenant_id,
            reply[:80],
        )
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind="boss_consult",
            messages=[text_message(reply)],
            access_token=channel_access_token,
        )

    else:  # CLARIFY
        logger.info(
            "tenant=%s [CLARIFY] question=%s",
            tenant_id,
            decision.clarify_question[:80],
        )
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=line_user_id,
            conversation_kind="follow_up",
            messages=[text_message(decision.clarify_question)],
            access_token=channel_access_token,
        )


async def _handle_event_logged(
    *,
    app: Any,
    event: dict[str, Any],
    tenant_id: str,
    repo: Any,
    onboarding_flow: OnboardingFlow,
    execute_dispatcher: AgentOSTaskDispatcher,
    consultant: LLMConsultant,
    sleep_query_service: SleepCustomerQueryService,
    policy_resolver: Any = None,
    channel_access_token: str = "",
) -> None:
    source = event.get("source", {})
    try:
        await _handle_event(
            app=app,
            event=event,
            tenant_id=tenant_id,
            repo=repo,
            onboarding_flow=onboarding_flow,
            execute_dispatcher=execute_dispatcher,
            consultant=consultant,
            sleep_query_service=sleep_query_service,
            policy_resolver=policy_resolver,
            channel_access_token=channel_access_token,
        )
    except Exception:
        logger.exception(
            "Unhandled LINE event error: tenant=%s type=%s user=%s",
            tenant_id,
            event.get("type", ""),
            source.get("userId", ""),
        )


def _resolve_line_channel_access_token(*, request: Request, tenant_id: str, repo: Any) -> str:
    config = repo.get_line_channel_config(tenant_id)
    settings = getattr(request.app.state, "settings", None)
    enc_key = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    if config is not None:
        token = decrypt_field(getattr(config, "channel_access_token", "") or "", enc_key)
        if token:
            return token
    return str(getattr(settings, "LINE_CHANNEL_ACCESS_TOKEN", "") or "").strip()


async def _handle_postback_event(
    *,
    request: Request,
    tenant_id: str,
    event: dict[str, Any],
    repo: Any,
    channel_access_token: str = "",
) -> dict[str, Any]:
    if event.get("type") != "postback":
        return {"status": "skipped", "reason": "not_postback"}

    source = event.get("source", {})
    actor_line_id = source.get("userId", "")
    data = parse_qs(event.get("postback", {}).get("data", ""), keep_blank_values=True)
    run_id = (data.get("run_id") or [""])[0]
    suggestion_id = (data.get("suggestion_id") or [""])[0]
    asset_intent_id = (data.get("asset_intent_id") or [""])[0]
    asset_decision = (data.get("decision") or [""])[0]
    action_raw = (data.get("action") or [""])[0]
    pending = repo.get_pending_approval_by_run_id(run_id) if run_id else None

    if action_raw == "asset_intent":
        pending_asset = repo.get_pending_asset_intent(asset_intent_id) if asset_intent_id else repo.get_latest_pending_asset_intent(
            tenant_id=tenant_id,
            line_user_id=actor_line_id,
        )
        if (
            pending_asset is None
            or str(getattr(pending_asset, "tenant_id", "") or "") != tenant_id
            or str(getattr(pending_asset, "line_user_id", "") or "") != actor_line_id
            or not _is_pending_asset_intent_active(pending_asset)
        ):
            if asset_intent_id:
                repo.resolve_pending_asset_intent(intent_id=asset_intent_id, status="expired")
            await _push_and_record_texts(
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=actor_line_id,
                conversation_kind="follow_up",
                messages=[text_message("這張照片的引導選項已過期，請重新傳一次圖片，我就會再帶你選一次。")],
                access_token=channel_access_token,
            )
            return {"status": "processed", "action": action_raw, "result": "expired"}
        try:
            reply_messages, conversation_kind, related_task_id, related_run_id = await _process_pending_asset_intent(
                app=request.app,
                repo=repo,
                tenant_id=tenant_id,
                line_user_id=actor_line_id,
                pending_asset=pending_asset,
                decision=asset_decision,
                execute_dispatcher=_get_execute_dispatcher(request),
                consultant=_get_consultant(request),
            )
        except UnsupportedExecutionIntentError:
            logger.exception("tenant=%s [ASSET_INTENT_POSTBACK] unsupported execute route", tenant_id)
            reply_messages = [text_message("這張圖目前還不能直接走這條流程，你可以改成先討論或換張圖。")]
            conversation_kind = "execute_result"
            related_task_id = ""
            related_run_id = ""
        except httpx.HTTPError:
            logger.exception("tenant=%s [ASSET_INTENT_POSTBACK] downstream HTTP error", tenant_id)
            reply_messages = [text_message("系統暫時無法接手這張圖，請稍後再試一次。")]
            conversation_kind = "execute_result"
            related_task_id = ""
            related_run_id = ""
        await _push_and_record_texts(
            repo=repo,
            tenant_id=tenant_id,
            line_user_id=actor_line_id,
            conversation_kind=conversation_kind,
            messages=reply_messages,
            access_token=channel_access_token,
            related_task_id=related_task_id,
            related_run_id=related_run_id,
        )
        return {"status": "processed", "action": action_raw, "decision": asset_decision}

    if action_raw in {"approve", "reject", "edit", "schedule_publish"}:
        result_message = ""
        if _is_local_pending_workflow(pending) and action_raw in {"approve", "reject"}:
            result_message = await _handle_local_pending_approval(
                app=request.app,
                repo=repo,
                tenant_id=tenant_id,
                pending=pending,
                action=ApprovalAction(action_raw),
                actor_line_id=actor_line_id,
            )
        elif _is_local_pending_workflow(pending) and action_raw == "schedule_publish":
            result_message = "這類本地草稿目前不支援二次排程，請先修改或直接發布。"
        else:
            bridge = _get_approval_bridge(request, repo)
            result = await bridge.handle_postback(
                run_id=run_id,
                tenant_id=tenant_id,
                action=ApprovalAction(action_raw),
                actor_line_id=actor_line_id,
            )
            result_message = result.message
        if result_message and actor_line_id:
            access_token = channel_access_token or request.app.state.settings.LINE_CHANNEL_ACCESS_TOKEN
            if access_token:
                pending_after = repo.get_pending_approval_by_run_id(run_id) if run_id else None
                await push_line_messages(
                    to=actor_line_id,
                    messages=[
                        text_message(
                            result_message
                            + (
                                "\n\n" + _build_approval_edit_prompt(pending_after.workflow_type)
                                if action_raw == "edit" and pending_after is not None
                                else ""
                            )
                        )
                    ],
                    access_token=access_token,
                )
        return {"status": "processed", "action": action_raw, "run_id": run_id}

    if action_raw == "suggestion_accept" and suggestion_id:
        await handle_suggestion_action(
            request=request,
            tenant_id=tenant_id,
            suggestion_id=suggestion_id,
            action="accept",
            actor_line_id=actor_line_id,
            execute_now=True,
        )
        return {"status": "processed", "action": action_raw, "suggestion_id": suggestion_id}

    if action_raw == "suggestion_dismiss" and suggestion_id:
        await handle_suggestion_action(
            request=request,
            tenant_id=tenant_id,
            suggestion_id=suggestion_id,
            action="dismiss",
            actor_line_id=actor_line_id,
        )
        return {"status": "processed", "action": action_raw, "suggestion_id": suggestion_id}

    return {"status": "skipped", "reason": "unsupported_postback_action", "action": action_raw}


async def replay_stored_line_webhook_event(*, request: Request, event: Any) -> dict[str, Any]:
    if str(getattr(event, "provider", "") or "") != "line":
        raise ValueError("only line webhook events are supported")

    try:
        payload = json.loads(getattr(event, "raw_payload_json", "") or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("stored webhook payload is invalid") from exc

    if not isinstance(payload, dict):
        raise ValueError("stored webhook payload must be an object")

    tenant_id = str(getattr(event, "tenant_id", "") or "").strip()
    repo = request.app.state.repository
    channel_access_token = _resolve_line_channel_access_token(request=request, tenant_id=tenant_id, repo=repo)
    event_type = str(payload.get("type", "") or getattr(event, "event_type", "") or "").strip()

    if event_type == "postback":
        result = await _handle_postback_event(
            request=request,
            tenant_id=tenant_id,
            event=payload,
            repo=repo,
            channel_access_token=channel_access_token,
        )
        return {"status": "replayed", "event_type": event_type, "result": result}

    onboarding_flow = OnboardingFlow(repo, website_ingestion_service=WebsiteKnowledgeIngestionService(repo))
    execute_dispatcher = _get_execute_dispatcher(request)
    consultant = _get_consultant(request)
    sleep_query_service = _get_sleep_query_service(request, repo)
    policy_resolver = getattr(request.app.state, "policy_resolver", None)
    await _handle_event_logged(
        app=request.app,
        event=payload,
        tenant_id=tenant_id,
        repo=repo,
        onboarding_flow=onboarding_flow,
        execute_dispatcher=execute_dispatcher,
        consultant=consultant,
        sleep_query_service=sleep_query_service,
        policy_resolver=policy_resolver,
        channel_access_token=channel_access_token,
    )
    return {"status": "replayed", "event_type": event_type}


@router.post("/line/{tenant_id}")
async def line_webhook(
    tenant_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: str = Header(default=""),
) -> dict[str, str]:
    """
    Multi-tenant LINE webhook endpoint。
    - 每個 tenant 有各自的 URL path（/webhooks/line/{tenant_id}）
    - channel_secret 從 kachu_line_channel_configs 按 tenant_id 查，不是全域 settings
    - channel_secret / channel_access_token 若有 FIELD_ENCRYPTION_KEY 則解密後使用
    """
    body = await request.body()

    repo = request.app.state.repository
    config = repo.get_line_channel_config(tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    # A-6：解密敏感欄位
    settings = getattr(request.app.state, "settings", None)
    enc_key = (getattr(settings, "FIELD_ENCRYPTION_KEY", "") or "").strip()
    channel_secret = decrypt_field(config.channel_secret, enc_key)
    channel_access_token = decrypt_field(config.channel_access_token, enc_key)

    if not _verify_line_signature(body, channel_secret, x_line_signature):
        raise HTTPException(status_code=403, detail="Invalid LINE signature")

    onboarding_flow = OnboardingFlow(repo, website_ingestion_service=WebsiteKnowledgeIngestionService(repo))
    execute_dispatcher = _get_execute_dispatcher(request)
    consultant = _get_consultant(request)
    sleep_query_service = _get_sleep_query_service(request, repo)
    policy_resolver = getattr(request.app.state, "policy_resolver", None)
    payload: dict[str, Any] = json.loads(body)
    for event in payload.get("events", []):
        dedupe_key = _line_event_dedupe_key(tenant_id, event)
        external_event_id, external_user_id, external_thread_id = _line_event_external_ids(event)
        if not repo.record_webhook_event_if_new(
            tenant_id=tenant_id,
            provider="line",
            dedupe_key=dedupe_key,
            event_type=str(event.get("type", "") or ""),
            raw_payload=event,
            external_event_id=external_event_id,
            external_user_id=external_user_id,
            external_thread_id=external_thread_id,
            occurred_at=_line_event_occurred_at(event),
        ):
            logger.info("tenant=%s duplicate LINE webhook skipped dedupe_key=%s", tenant_id, dedupe_key)
            continue
        if str(event.get("type", "") or "") == "postback":
            await _handle_postback_event(
                request=request,
                tenant_id=tenant_id,
                event=event,
                repo=repo,
                channel_access_token=channel_access_token,
            )
            continue
        background_tasks.add_task(
            _handle_event_logged,
            app=request.app,
            event=event,
            tenant_id=tenant_id,
            repo=repo,
            onboarding_flow=onboarding_flow,
            execute_dispatcher=execute_dispatcher,
            consultant=consultant,
            sleep_query_service=sleep_query_service,
            policy_resolver=policy_resolver,
            channel_access_token=channel_access_token,
        )

    return {"status": "ok"}


@router.post("/line/{tenant_id}/postback")
async def line_postback(tenant_id: str, request: Request) -> dict[str, str]:
    body = await request.body()
    repo = request.app.state.repository
    config = repo.get_line_channel_config(tenant_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Tenant not found")

    signature = request.headers.get("X-Line-Signature", "")
    enc_key = request.app.state.settings.FIELD_ENCRYPTION_KEY
    channel_secret = decrypt_field(config.channel_secret, enc_key)
    channel_access_token = decrypt_field(config.channel_access_token, enc_key)
    if not _verify_line_signature(body, channel_secret, signature):
        raise HTTPException(status_code=403, detail="Invalid LINE signature")

    payload: dict[str, Any] = json.loads(body)
    for event in payload.get("events", []):
        dedupe_key = _line_event_dedupe_key(tenant_id, event)
        external_event_id, external_user_id, external_thread_id = _line_event_external_ids(event)
        if not repo.record_webhook_event_if_new(
            tenant_id=tenant_id,
            provider="line",
            dedupe_key=dedupe_key,
            event_type=str(event.get("type", "") or ""),
            raw_payload=event,
            external_event_id=external_event_id,
            external_user_id=external_user_id,
            external_thread_id=external_thread_id,
            occurred_at=_line_event_occurred_at(event),
        ):
            logger.info("tenant=%s duplicate LINE postback skipped dedupe_key=%s", tenant_id, dedupe_key)
            continue
        if event.get("type") != "postback":
            continue
        await _handle_postback_event(
            request=request,
            tenant_id=tenant_id,
            event=event,
            repo=repo,
            channel_access_token=channel_access_token,
        )
    return {"status": "ok"}


@router.post("/line/{tenant_id}/approval-edit/{run_id}")
async def complete_approval_edit(tenant_id: str, run_id: str, request: Request) -> dict[str, str]:
    repo = request.app.state.repository
    if repo.get_tenant(tenant_id) is None:
        raise HTTPException(status_code=404, detail="Tenant not found")
    payload = await request.json()
    actor_line_id = str(payload.get("actor_line_id", "owner"))
    edited_payload = dict(payload.get("edited_payload", {}))
    bridge = _get_approval_bridge(request, repo)
    await bridge.complete_edit_and_approve(
        run_id=run_id,
        actor_line_id=actor_line_id,
        edited_payload=edited_payload,
    )
    return {"status": "ok"}
