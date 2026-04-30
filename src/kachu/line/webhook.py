from __future__ import annotations
import asyncio
import base64
import hashlib
import hmac
import logging
from typing import Any
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request
from pydantic import ValidationError
from ..agentOS_client import AgentOSClient
from ..approval_bridge import ApprovalBridge
from ..business_consultant import BusinessConsultant
from ..config import Settings
from ..intent_router import IntentRouter
from ..memory import MemoryManager
from ..models import ApprovalAction, Intent
from ..onboarding import OnboardingFlow
from ..persistence import KachuRepository
from .push import push_line_messages, text_message



logger = logging.getLogger(__name__)



router = APIRouter(prefix="/webhooks", tags=["line"])





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

            _handle_event,

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



        if msg_type == "image":

            line_message_id = message.get("id", "")

            photo_url = ""

            if settings.LINE_CHANNEL_ACCESS_TOKEN and line_message_id:

                try:

                    image_bytes = await _download_line_content_with_retry(

                        line_message_id, settings.LINE_CHANNEL_ACCESS_TOKEN

                    )

                    photo_url = "data:image/jpeg;base64," + base64.b64encode(image_bytes).decode()

                except httpx.HTTPError as _exc:

                    logger.warning("Failed to download LINE image %s: %s", line_message_id, _exc)

                    await _notify_processing_failure(

                        line_user_id=line_user_id,

                        settings=settings,

                        text="照片下載失敗，請重新上傳後再試一次。",

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



        elif msg_type == "text":

            boss_text_2: str = message.get("text", "")

            repo.save_conversation(

                tenant_id=tenant_id,

                role="owner",

                content=boss_text_2,

                conversation_type="general",

            )

            if context_brief_manager is not None:

                await context_brief_manager.refresh_briefs(

                    tenant_id,

                    reason="boss_general_message",

                )

            # Phase 2: Use LLM classification for full intent coverage

            intent, topic = await intent_router.classify_text_llm(boss_text_2)

            if intent in (Intent.KNOWLEDGE_UPDATE, Intent.GOOGLE_POST,

                          Intent.GA4_REPORT, Intent.REVIEW_REPLY):

                await intent_router.dispatch(

                    intent=intent,

                    tenant_id=tenant_id,

                    trigger_source="line",

                    trigger_payload={"message": boss_text_2, "topic": topic},

                )

            elif intent == Intent.FAQ_QUERY:

                logger.info("Boss sent FAQ-like text; skipping FAQ workflow for boss")

            else:

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
                        conversation_type="general",
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

    """Handle boss text reply during an active edit session."""

    skip_keywords = {"跳過", "skip", "next", "略過"}



    if edit_session.step == "waiting_ig":

        if text.strip() not in skip_keywords:

            # Store in memory for preference learning

            memory_manager.store_preference(

                tenant_id=tenant_id,

                platform="ig_fb",

                original_draft=edit_session.original_ig_draft,

                edited_draft=text,

                run_id=edit_session.run_id,

            )

            # Store in EditSession record

            repo.update_edit_session_draft(edit_session.id, "ig_fb", text)

        else:

            # Boss chose to skip, use original draft

            repo.update_edit_session_draft(edit_session.id, "ig_fb", edit_session.original_ig_draft)



        repo.advance_edit_session(edit_session.id, "waiting_google")



        if settings.LINE_CHANNEL_ACCESS_TOKEN:

            try:

                await push_line_messages(

                    to=line_user_id,

                    messages=[

                        text_message(

                            "✅ IG 文本已儲存\n\n"

                            "請輸入 Google 商家文本：\n"

                            "（如果不需要修改，輸入「跳過」）"

                        )

                    ],

                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

                )

            except httpx.HTTPError as exc:

                logger.error("Failed to push Google edit prompt: %s", exc)



    elif edit_session.step == "waiting_google":

        if text.strip() not in skip_keywords:

            memory_manager.store_preference(

                tenant_id=tenant_id,

                platform="google",

                original_draft=edit_session.original_google_draft,

                edited_draft=text,

                run_id=edit_session.run_id,

            )

            # Store in EditSession record

            repo.update_edit_session_draft(edit_session.id, "google", text)

        else:

            # Boss chose to skip, use original draft

            repo.update_edit_session_draft(edit_session.id, "google", edit_session.original_google_draft)



        # Fetch fresh EditSession record to get both edited drafts

        updated_edit_session = repo.get_active_edit_session(tenant_id)



        # Assemble edited_payload with corrected drafts

        edited_ig_draft = updated_edit_session.edited_ig_draft if updated_edit_session else edit_session.original_ig_draft

        edited_google_draft = updated_edit_session.edited_google_draft if updated_edit_session else edit_session.original_google_draft

        

        # Submit edited_payload approval to AgentOS

        submitted = await approval_bridge.complete_edit_and_approve(

            run_id=edit_session.run_id,

            actor_line_id=line_user_id,

            edited_ig_draft=edited_ig_draft,

            edited_google_draft=edited_google_draft,

        )

        if submitted:

            repo.complete_edit_session(edit_session.id)



        # Record episode: boss chose to edit

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

                    messages=[

                        text_message(

                            "✅ 很好，已儲存！\n\n"

                            "下次 AI 生成草稿將更符合您的風格。"

                        )

                    ],

                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,

                )

            except httpx.HTTPError as exc:

                logger.error("Failed to push edit completion message: %s", exc)



async def _download_line_image(message_id: str, access_token: str) -> bytes:

    """Download image binary from LINE Content API."""

    async with httpx.AsyncClient(timeout=15.0) as client:

        resp = await client.get(

            f"https://api-data.line.me/v2/bot/message/{message_id}/content",

            headers={"Authorization": f"Bearer {access_token}"},

        )

        resp.raise_for_status()

        return resp.content
