from __future__ import annotations

import base64
import binascii
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from ..line.flex_builder import build_ga4_report_flex, build_google_post_flex, build_knowledge_update_flex, build_photo_content_flex, build_review_reply_flex
from ..line.push import push_line_messages, text_message
from ..llm import analyze_image_bytes, analyze_image_url, generate_text
from ..models import (
    AnalyzePhotoRequest,
    AnalyzeSentimentRequest,
    ApplyKnowledgeUpdateRequest,
    CheckDraftDirectionRequest,
    ClassifyMessageRequest,
    DeterminePostTypeRequest,
    DiffKnowledgeRequest,
    FetchGA4DataRequest,
    FetchReviewRequest,
    GenerateDraftsRequest,
    GenerateGA4InsightsRequest,
    GenerateGooglePostRequest,
    GenerateRecommendationsRequest,
    GenerateResponseRequest,
    GenerateReviewReplyRequest,
    NotifyApprovalRequest,
    ParseKnowledgeUpdateRequest,
    PostReviewReplyRequest,
    PublishContentRequest,
    PublishGooglePostRequest,
    RetrieveAnswerRequest,
    RetrieveContextRequest,
    SendGA4ReportRequest,
    SendOrEscalateRequest,
)
from ..memory import MemoryManager
from ..persistence import KachuRepository

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tools", tags=["tools"])


# ── Helpers ───────────────────────────────────────────────────────────────────


def _strip_json_fence(raw: str) -> str:
    """Remove optional ```json ... ``` code fence from LLM output."""
    text = raw.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _degraded_photo_analysis(
    *,
    line_message_id: str,
    scene_description: str,
    error_code: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "analysis_id": f"analysis-{line_message_id}",
        "scene_description": scene_description,
        "detected_objects": [],
        "suggested_tags": [],
        "quality_score": 0.0,
        "status": "degraded",
        "needs_manual_review": True,
        "error_code": error_code,
        "fallback_reason": reason,
    }


class RecoverableToolError(Exception):
    """Errors that can safely fall back to degraded or stub behavior."""


def _is_recoverable_llm_service_error(exc: Exception) -> bool:
    if isinstance(exc, (httpx.HTTPError, TimeoutError)):
        return True
    if isinstance(exc, ModuleNotFoundError):
        return exc.name in {"litellm", "openai", "anthropic"}
    module_root = exc.__class__.__module__.split(".", 1)[0]
    return module_root in {"litellm", "openai", "anthropic", "google"}


def _parse_llm_json(raw: str, *, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(_strip_json_fence(raw))
    except json.JSONDecodeError as exc:
        raise RecoverableToolError(f"{operation} returned invalid JSON") from exc

    if not isinstance(payload, dict):
        raise RecoverableToolError(f"{operation} returned a non-object JSON payload")
    return payload


def _is_recoverable_external_api_error(exc: Exception, *, module_roots: set[str]) -> bool:
    if isinstance(exc, (httpx.HTTPError, OSError, ValueError)):
        return True
    if isinstance(exc, ModuleNotFoundError):
        return bool(exc.name) and exc.name.split(".", 1)[0] in module_roots
    return exc.__class__.__module__.split(".", 1)[0] in module_roots


async def _run_external_sync_call(
    *,
    operation: str,
    func,
    module_roots: set[str],
):
    import asyncio

    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, func)
    except Exception as exc:
        if _is_recoverable_external_api_error(exc, module_roots=module_roots):
            raise RecoverableToolError(f"{operation} failed: {exc}") from exc
        raise


def _repo(request: Request) -> KachuRepository:
    return request.app.state.repository


def _settings(request: Request):
    return request.app.state.settings


def _memory(request: Request) -> MemoryManager:
    return request.app.state.memory_manager


async def _llm(
    *,
    prompt: str,
    system: str = "",
    model: str,
    api_key: str = "",
    openai_api_key: str = "",
    run_id: str | None = None,
    generation_name: str | None = None,
) -> str:
    """Thin wrapper around generate_text that propagates run_id for Langfuse tracing."""
    try:
        return await generate_text(
            prompt=prompt,
            system=system,
            model=model,
            api_key=api_key,
            openai_api_key=openai_api_key,
            run_id=run_id,
            generation_name=generation_name,
        )
    except Exception as exc:
        if _is_recoverable_llm_service_error(exc):
            raise RecoverableToolError(str(exc)) from exc
        raise


# ── Photo Content: analyze-photo ──────────────────────────────────────────────


@router.post("/analyze-photo")
async def analyze_photo(body: AnalyzePhotoRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Gemini Vision analyzes the photo with explicit degraded fallback."""
    settings = _settings(request)

    if not body.photo_url:
        return _degraded_photo_analysis(
            line_message_id=body.line_message_id,
            scene_description="照片未提供，請重新上傳後再試一次。",
            error_code="missing_photo_url",
            reason="No photo_url provided",
        )

    if not settings.GOOGLE_AI_API_KEY:
        return _degraded_photo_analysis(
            line_message_id=body.line_message_id,
            scene_description="照片已接收，但目前影像分析服務未啟用，需人工確認內容。",
            error_code="vision_unconfigured",
            reason="GOOGLE_AI_API_KEY is not configured",
        )

    prompt = (
        "你是一位專業社群媒體行銷人員。請分析這張照片並用繁體中文回覆：\n"
        "1. 場景描述（50字內）\n"
        "2. 主要物件（最多5個，逗號分隔）\n"
        "3. 適合的社群標籤（最多6個，#開頭）\n"
        "4. 照片品質評分（0.0-1.0）\n"
        "請以 JSON 格式回覆，欄位名稱：scene_description, detected_objects(list), suggested_tags(list), quality_score"
    )
    try:
        if body.photo_url.startswith("data:"):
            header, b64data = body.photo_url.split(",", 1)
            mime_type = header.split(";")[0].split(":")[1] if ":" in header else "image/jpeg"
            image_bytes = base64.b64decode(b64data)
            raw = await analyze_image_bytes(
                image_bytes=image_bytes,
                mime_type=mime_type,
                prompt=prompt,
                api_key=settings.GOOGLE_AI_API_KEY,
            )
        else:
            raw = await analyze_image_url(
                image_url=body.photo_url,
                prompt=prompt,
                api_key=settings.GOOGLE_AI_API_KEY,
            )
        result = _parse_llm_json(raw, operation="analyze-photo")
        return {
            "analysis_id": f"analysis-{body.line_message_id}",
            **result,
            "status": "analyzed",
            "needs_manual_review": False,
        }
    except (RecoverableToolError, ValueError, binascii.Error, httpx.HTTPError, TimeoutError) as exc:
        logger.warning("Gemini Vision failed, returning degraded analysis: %s", exc)
        return _degraded_photo_analysis(
            line_message_id=body.line_message_id,
            scene_description="照片已接收，但 AI 影像分析失敗，需人工確認內容。",
            error_code="vision_analysis_failed",
            reason=str(exc),
        )
    except ModuleNotFoundError as exc:
        if not _is_recoverable_llm_service_error(exc):
            raise
        logger.warning("Gemini Vision failed, returning degraded analysis: %s", exc)
        return _degraded_photo_analysis(
            line_message_id=body.line_message_id,
            scene_description="照片已接收，但 AI 影像分析失敗，需人工確認內容。",
            error_code="vision_analysis_failed",
            reason=str(exc),
        )


# ── Photo Content: retrieve-context ───────────────────────────────────────────


@router.post("/retrieve-context")
async def retrieve_context(body: RetrieveContextRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Retrieve brand knowledge with semantic search (falls back to keyword)."""
    repo = _repo(request)
    memory = _memory(request)

    # Semantic search returns ranked entries relevant to the query
    ranked = await memory.retrieve_relevant_knowledge(
        tenant_id=body.tenant_id,
        query=body.query,
        top_k=12,
    )

    # Also load all entries for structured fields (basic_info, style) which are
    # always needed regardless of query relevance.
    all_entries = repo.get_knowledge_entries(body.tenant_id)

    def _entries_of(cat: str) -> list[str]:
        return [e.content for e in all_entries if e.category == cat]

    basic = _entries_of("basic_info")
    brand_name = brand_industry = brand_address = ""
    if basic:
        for part in basic[0].split("\n"):
            if part.startswith("店名："):
                brand_name = part[3:]
            elif part.startswith("行業："):
                brand_industry = part[3:]
            elif part.startswith("地址："):
                brand_address = part[3:]

    if not brand_name:
        tenant = repo.get_or_create_tenant(body.tenant_id)
        brand_name = tenant.name
        brand_industry = tenant.industry_type
        brand_address = tenant.address

    style_entries = _entries_of("style")

    # Collect semantically relevant facts (top ranked, non-structural categories)
    structural_cats = {"basic_info", "style"}
    relevant_facts = [
        e["content"]
        for e in ranked
        if e["category"] not in structural_cats
    ][:6]

    # Collect preference hints (recent boss edit examples, per platform)
    ig_pref_hints = memory.get_preference_examples(body.tenant_id, "ig_fb", limit=2)
    google_pref_hints = memory.get_preference_examples(body.tenant_id, "google", limit=2)

    # Collect episode hints (recent outcomes for this workflow type)
    episode_hints = memory.get_recent_episodes(
        body.tenant_id,
        workflow_type=body.workflow_type or None,
        limit=5,
    )

    # Phase 5: inject SharedContext hints (GA4 recommendations, monthly calendar topic)
    repo = _repo(request)
    shared_context_hints: dict = {}
    ga4_ctx = repo.get_shared_context(body.tenant_id, "ga4_recommendations")
    if ga4_ctx:
        shared_context_hints["ga4_recommendations"] = ga4_ctx.get("recommendations", [])
    calendar_ctx = repo.get_shared_context(body.tenant_id, "monthly_content_calendar")
    if calendar_ctx and calendar_ctx.get("weeks"):
        from datetime import datetime, timezone as _tz
        week_idx = min((datetime.now(_tz.utc).day - 1) // 7, len(calendar_ctx["weeks"]) - 1)
        shared_context_hints["calendar_topic"] = calendar_ctx["weeks"][week_idx].get("topic", "")

    return {
        "brand_name": brand_name or "（未設定）",
        "brand_industry": brand_industry,
        "brand_address": brand_address,
        "brand_tone": style_entries[0] if style_entries else "親切真誠、在地溫暖",
        "core_values": _entries_of("core_value"),
        "pain_points": _entries_of("pain_point"),
        "goals": _entries_of("goal"),
        "relevant_facts": relevant_facts or [e.content for e in all_entries if e.category in ("product", "contact")],
        "preference_hints": {"ig_fb": ig_pref_hints, "google": google_pref_hints},
        "episode_hints": episode_hints,
        "shared_context_hints": shared_context_hints,
    }


@router.post("/check-draft-direction")
async def check_draft_direction(
    body: CheckDraftDirectionRequest, request: Request
) -> dict[str, Any]:
    """Build a preflight direction brief before draft generation for low-trust tenants."""
    settings = _settings(request)
    analysis = body.analysis or {}
    context = body.context or {}

    scene = analysis.get("scene_description", "")
    brand_name = context.get("brand_name", "這家店")
    brand_tone = context.get("brand_tone", "親切真誠")
    goals = context.get("goals", [])
    relevant_facts = context.get("relevant_facts", [])
    shared_hints = context.get("shared_context_hints", {})
    calendar_topic = shared_hints.get("calendar_topic", "")

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            prompt = (
                f"你是 {brand_name} 的資深品牌編輯。請在正式寫文案前，先產生一份『文案方向確認』。\n"
                f"品牌語氣：{brand_tone}\n"
                f"照片場景：{scene}\n"
                f"品牌目標：{'、'.join(goals) if goals else '未提供'}\n"
                f"相關事實：{'；'.join(relevant_facts[:4]) if relevant_facts else '未提供'}\n"
                f"本月主題：{calendar_topic or '未提供'}\n\n"
                "請以 JSON 回覆："
                '{"direction_summary": "...", "focus_points": ["..."], "avoidances": ["..."]}'
            )
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="check-draft-direction",
            )
            payload = _parse_llm_json(raw, operation="check-draft-direction")
            payload.setdefault("direction_summary", f"延續 {brand_tone} 調性，聚焦 {scene or '本次主題'}。")
            payload.setdefault("focus_points", [calendar_topic] if calendar_topic else [])
            payload.setdefault("avoidances", ["避免過度制式與空泛形容"])
            return payload
        except RecoverableToolError as exc:
            logger.warning("check-draft-direction LLM failed, using fallback: %s", exc)

    summary_parts = [part for part in [calendar_topic, scene] if part]
    direction_summary = "，".join(summary_parts) or "延續品牌語氣，聚焦本次照片亮點。"
    focus_points = [item for item in [calendar_topic, *relevant_facts[:2]] if item][:3]
    return {
        "direction_summary": direction_summary,
        "focus_points": focus_points,
        "avoidances": ["避免過度制式與空泛形容"],
    }


# ── Photo Content: generate-drafts ────────────────────────────────────────────


@router.post("/generate-drafts")
async def generate_drafts(body: GenerateDraftsRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Use LiteLLM (Gemini/OpenAI) to generate IG/FB + Google post drafts."""
    settings = _settings(request)
    analysis = body.analysis or {}
    context = body.context or {}

    brand_name = context.get("brand_name", "")
    brand_tone = context.get("brand_tone", "親切真誠")
    core_values = context.get("core_values", [])
    brand_address = context.get("brand_address", "")
    scene = analysis.get("scene_description", "")
    tags = analysis.get("suggested_tags", [])

    core_values_str = "、".join(core_values) if core_values else "尚未設定"
    tags_str = " ".join(tags)
    address_hint = f"📍 {brand_address}" if brand_address else ""

    system_prompt = (
        f"你是 {brand_name or '這家店'} 的社群媒體小編，風格「{brand_tone}」。\n"
        f"品牌核心價值：{core_values_str}\n"
        "請用繁體中文撰寫貼文，語氣自然，適度行銷。\n"
    )
    ig_prompt = (
        f"請以下列場景資訊，撰寫一篇 IG/FB 貼文（200字以內）：\n"
        f"場景描述：{scene}\n建議標籤：{tags_str}\n{address_hint}\n\n"
        "要求：第一行寫吸引人的標題（可用 emoji），內容自然真誠，結尾附上 2-4 個 hashtag"
    )
    google_prompt = (
        f"請以下列場景資訊，撰寫一篇 Google 商家貼文（150字以內，商業風格，不用 emoji）：\n"
        f"場景描述：{scene}\n品牌：{brand_name}，地址：{brand_address}"
    )

    # ── Inject preference few-shot examples ───────────────────────────────────
    memory = _memory(request)
    ig_prefs = memory.get_preference_examples(body.tenant_id, "ig_fb", limit=2)
    google_prefs = memory.get_preference_examples(body.tenant_id, "google", limit=2)

    if ig_prefs:
        ig_prompt += "\n\n【參考：老闆過去的修改風格】\n"
        for p in ig_prefs:
            ig_prompt += f"原版：{p['original'][:120]}\n老闆改為：{p['edited'][:120]}\n備註：{p['notes']}\n---\n"

    if google_prefs:
        google_prompt += "\n\n【參考：老闆過去的修改風格】\n"
        for p in google_prefs:
            google_prompt += f"原版：{p['original'][:120]}\n老闆改為：{p['edited'][:120]}\n---\n"

    # Inject episode hints (recent approval outcomes) to inform tone
    episode_hints: list[dict] = context.get("episode_hints", [])
    if episode_hints:
        recent_rejections = sum(1 for e in episode_hints if e.get("outcome") == "rejected")
        recent_approvals = sum(1 for e in episode_hints if e.get("outcome") == "approved")
        if recent_rejections >= 2 and recent_rejections > recent_approvals:
            ig_prompt += (
                "\n\n【注意：老闆最近多次拒絕草稿，請特別注意文案品質，語氣更貼近品牌風格，避免過於制式。】"
            )
            google_prompt += (
                "\n\n【注意：老闆最近多次拒絕草稿，請確保商業風格自然、符合品牌調性。】"
            )

    # Phase 4: inject policy context (low-trust extra instruction)
    workflow_input = body.workflow_input or {}
    policy_ctx: str = workflow_input.get("policy_generation_context", "")
    if not policy_ctx:
        policy_ctx = context.get("policy_generation_context", "")
    if policy_ctx:
        ig_prompt += f"\n\n{policy_ctx}"
        google_prompt += f"\n\n{policy_ctx}"

    # Phase 5: inject SharedContext topic hint
    shared_hints = context.get("shared_context_hints", {})
    calendar_topic: str = shared_hints.get("calendar_topic", "")
    if calendar_topic:
        ig_prompt += f"\n\n【本月行銷主題方向：{calendar_topic}，請在文案中融入此主題。】"
        google_prompt += f"\n\n【本月行銷主題方向：{calendar_topic}】"

    direction_check = context.get("direction_check", {})
    direction_summary = direction_check.get("direction_summary", "")
    if direction_summary:
        focus_points = "、".join(direction_check.get("focus_points", []))
        avoidances = "、".join(direction_check.get("avoidances", []))
        ig_prompt += (
            f"\n\n【生成前方向確認】{direction_summary}"
            f"\n請特別強調：{focus_points or '照片主亮點'}"
            f"\n請避免：{avoidances or '過度空泛描述'}"
        )
        google_prompt += (
            f"\n\n【生成前方向確認】{direction_summary}"
            f"\n請特別強調：{focus_points or '照片主亮點'}"
            f"\n請避免：{avoidances or '過度空泛描述'}"
        )

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            import asyncio
            ig_task = asyncio.create_task(_llm(prompt=ig_prompt, system=system_prompt, model=settings.LITELLM_MODEL, api_key=settings.GOOGLE_AI_API_KEY, openai_api_key=settings.OPENAI_API_KEY, run_id=body.run_id, generation_name="generate-drafts-ig"))
            google_task = asyncio.create_task(_llm(prompt=google_prompt, system=system_prompt, model=settings.LITELLM_MODEL, api_key=settings.GOOGLE_AI_API_KEY, openai_api_key=settings.OPENAI_API_KEY, run_id=body.run_id, generation_name="generate-drafts-google"))
            ig_text, google_text = await asyncio.gather(ig_task, google_task)
            return {"ig_fb": ig_text, "google": google_text}
        except RecoverableToolError as exc:
            logger.warning("LLM draft generation failed, using stub: %s", exc)

    return {
        "ig_fb": f"✨ {brand_name} 新鮮出爐！\n\n{scene or '精心製作的新品，等你來品嚐'}\n\n{address_hint}\n{tags_str}",
        "google": f"【{brand_name}】新品上市\n{scene or '歡迎蒞臨品嚐我們最新推出的商品。'}\n{brand_address}",
    }


@router.post("/notify-approval")
async def notify_approval(body: NotifyApprovalRequest, request: Request) -> dict[str, Any]:
    """
    Store PendingApproval and push LINE Flex Message to boss.
    """
    repo = _repo(request)
    settings = request.app.state.settings

    # Store pending approval in DB
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    approval_record = repo.create_pending_approval(
        tenant_id=body.tenant_id,
        agentos_run_id=body.run_id,
        workflow_type=body.workflow,
        draft_content=body.drafts,
        expires_at=expires_at,
    )
    repo.save_audit_event(
        tenant_id=body.tenant_id,
        agentos_run_id=body.run_id,
        workflow_type=body.workflow,
        event_type="approval_requested",
        source="notify_approval",
        payload={"approval_record_id": approval_record.id, "expires_at": expires_at.isoformat()},
    )

    # Push LINE Flex Message — check rate limit first
    boss_user_id = settings.LINE_BOSS_USER_ID
    if boss_user_id and settings.LINE_CHANNEL_ACCESS_TOKEN:
        # Check daily push limit and quiet hours
        tenant = repo.get_or_create_tenant(body.tenant_id)
        if repo.can_push(
            body.tenant_id,
            max_per_day=settings.MAX_PUSH_PER_DAY,
            quiet_hours_start=tenant.quiet_hours_start,
            quiet_hours_end=tenant.quiet_hours_end,
        ):
            try:
                await _push_flex_to_boss(
                    run_id=body.run_id,
                    tenant_id=body.tenant_id,
                    workflow=body.workflow,
                    drafts=body.drafts,
                    boss_user_id=boss_user_id,
                    channel_access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
                repo.record_push(
                    tenant_id=body.tenant_id,
                    recipient_line_id=boss_user_id,
                    message_type="approval",
                )
                repo.save_audit_event(
                    tenant_id=body.tenant_id,
                    agentos_run_id=body.run_id,
                    workflow_type=body.workflow,
                    event_type="push_sent",
                    source="notify_approval",
                    payload={"message_type": "approval", "recipient_line_id": boss_user_id},
                )
            except httpx.HTTPError as exc:
                logger.error("Failed to push LINE Flex notification: %s", exc)
                repo.save_audit_event(
                    tenant_id=body.tenant_id,
                    agentos_run_id=body.run_id,
                    workflow_type=body.workflow,
                    event_type="push_failed",
                    source="notify_approval",
                    payload={"message_type": "approval", "error": str(exc)},
                )
        else:
            logger.warning(
                "Push suppressed (rate limit or quiet hours) for run_id=%s", body.run_id
            )
            repo.save_audit_event(
                tenant_id=body.tenant_id,
                agentos_run_id=body.run_id,
                workflow_type=body.workflow,
                event_type="push_suppressed",
                source="notify_approval",
                payload={"message_type": "approval", "reason": "rate_limit_or_quiet_hours"},
            )
    else:
        logger.warning(
            "LINE credentials not configured; skipping push for run_id=%s", body.run_id
        )
        repo.save_audit_event(
            tenant_id=body.tenant_id,
            agentos_run_id=body.run_id,
            workflow_type=body.workflow,
            event_type="push_skipped",
            source="notify_approval",
            payload={"message_type": "approval", "reason": "line_not_configured"},
        )

    return {"status": "notified", "approval_record_id": approval_record.id}


async def _push_flex_to_boss(
    *,
    run_id: str,
    tenant_id: str,
    workflow: str,
    drafts: dict[str, Any],
    boss_user_id: str,
    channel_access_token: str,
) -> None:
    import httpx

    if workflow in ("kachu_photo_content", "photo_content"):
        flex_content = build_photo_content_flex(run_id=run_id, tenant_id=tenant_id, drafts=drafts)
    elif workflow in ("kachu_review_reply", "review_reply"):
        review_text = drafts.get("review_content", "")
        reply_text = drafts.get("reply_draft", "")
        flex_content = build_review_reply_flex(
            run_id=run_id, tenant_id=tenant_id, review_content=review_text, reply_draft=reply_text
        )
    elif workflow in ("kachu_knowledge_update", "knowledge_update"):
        flex_content = build_knowledge_update_flex(run_id=run_id, tenant_id=tenant_id, drafts=drafts)
    elif workflow in ("kachu_google_post", "google_post"):
        post_text = drafts.get("post_text", "")
        flex_content = build_google_post_flex(run_id=run_id, tenant_id=tenant_id, post_text=post_text)
    else:
        flex_content = build_photo_content_flex(run_id=run_id, tenant_id=tenant_id, drafts=drafts)

    push_body = {
        "to": boss_user_id,
        "messages": [
            {
                "type": "flex",
                "altText": "新任務草稿準備好了，請確認",
                "contents": flex_content,
            }
        ],
    }

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://api.line.me/v2/bot/message/push",
            headers={
                "Authorization": f"Bearer {channel_access_token}",
                "Content-Type": "application/json",
            },
            content=json.dumps(push_body, ensure_ascii=False).encode(),
            timeout=10.0,
        )
        resp.raise_for_status()


@router.post("/publish-content")
async def publish_content(body: PublishContentRequest, request: Request) -> dict[str, Any]:
    """Publish to connected platforms. Supports Google Business Profile and Meta (IG/FB)."""
    repo = _repo(request)
    settings = _settings(request)
    results: dict[str, Any] = {}
    repo.save_audit_event(
        tenant_id=body.tenant_id,
        agentos_run_id=body.run_id,
        workflow_type="photo_content",
        event_type="publish_attempted",
        source="publish_content",
        payload={"selected_platforms": body.selected_platforms},
    )

    if "google" in body.selected_platforms:
        google_text = body.drafts.get("google", "")
        if google_text and settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GOOGLE_BUSINESS_ACCOUNT_ID and settings.GOOGLE_BUSINESS_LOCATION_ID:
            try:
                from ..google import GoogleBusinessClient
                gbp = GoogleBusinessClient(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
                gbp_result = await _run_external_sync_call(
                    operation="publish-content/google",
                    func=lambda: gbp.create_local_post(
                        account_id=settings.GOOGLE_BUSINESS_ACCOUNT_ID,
                        location_id=settings.GOOGLE_BUSINESS_LOCATION_ID,
                        summary=google_text,
                    ),
                    module_roots={"google"},
                )
                results["google"] = {"status": "published", "post_name": gbp_result.get("name")}
            except RecoverableToolError as exc:
                logger.error("Google Business post failed: %s", exc)
                results["google"] = {"status": "failed", "error": str(exc)}
        else:
            results["google"] = {"status": "skipped", "reason": "credentials or text missing"}

    if "ig_fb" in body.selected_platforms:
        meta_account = repo.get_connector_account(body.tenant_id, "meta")
        if meta_account and meta_account.credentials_encrypted:
            try:
                import json as _json
                from ..meta import MetaClient, MetaAPIError
                creds = _json.loads(meta_account.credentials_encrypted)
                meta = MetaClient(
                    access_token=creds.get("access_token", ""),
                    ig_user_id=creds.get("ig_user_id") or None,
                    fb_page_id=creds.get("fb_page_id") or None,
                )
                caption = body.drafts.get("ig_fb", "") or body.drafts.get("ig", "")
                image_url = body.drafts.get("image_url", "")
                meta_results: dict[str, Any] = {}

                if image_url and creds.get("ig_user_id"):
                    ig_result = await meta.post_ig_photo(image_url=image_url, caption=caption)
                    meta_results["instagram"] = {"status": "published", **ig_result}
                elif creds.get("ig_user_id"):
                    meta_results["instagram"] = {"status": "skipped", "reason": "no image_url for IG"}

                if image_url and creds.get("fb_page_id"):
                    fb_result = await meta.post_fb_photo(image_url=image_url, message=caption)
                    meta_results["facebook"] = {"status": "published", **fb_result}
                elif creds.get("fb_page_id") and caption:
                    fb_result = await meta.post_fb_text(message=caption)
                    meta_results["facebook"] = {"status": "published", **fb_result}
                else:
                    meta_results["facebook"] = {"status": "skipped", "reason": "no content or credentials"}

                results["ig_fb"] = {"status": "done", **meta_results}
            except (MetaAPIError, httpx.HTTPError, json.JSONDecodeError, ValueError) as exc:
                logger.error("Meta publish failed: %s", exc)
                results["ig_fb"] = {"status": "failed", "error": str(exc)}
        else:
            results["ig_fb"] = {"status": "skipped_no_credentials", "reason": "Meta account not connected"}

    for platform in body.selected_platforms:
        if platform not in ("google", "ig_fb") and platform not in results:
            results[platform] = {"status": "recorded", "note": "platform not yet implemented"}

    repo.decide_pending_approval(agentos_run_id=body.run_id, decision="published", actor_line_id="system")
    wf_record = repo.get_workflow_record_by_run_id(body.run_id)
    if wf_record:
        repo.update_workflow_record_status(wf_record.id, "completed")

    # Record episode: boss approved and content was published
    memory = _memory(request)
    memory.record_episode(
        tenant_id=body.tenant_id,
        workflow_type="photo_content",
        outcome="approved_published",
        context_summary={
            "run_id": body.run_id,
            "platforms": body.selected_platforms,
            "publish_results": {k: v.get("status") for k, v in results.items()},
        },
    )

    result_statuses = [item.get("status") for item in results.values()]
    event_type = "publish_failed" if any(status == "failed" for status in result_statuses) else "publish_succeeded"
    if result_statuses and all(status in {"skipped", "skipped_no_credentials"} for status in result_statuses):
        event_type = "publish_skipped"
    repo.save_audit_event(
        tenant_id=body.tenant_id,
        agentos_run_id=body.run_id,
        workflow_type="photo_content",
        event_type=event_type,
        source="publish_content",
        payload={"results": results},
    )

    return {"status": "done", "run_id": body.run_id, "results": results}


# ── Review Reply Workflow tools ───────────────────────────────────────────────


@router.post("/fetch-review")
async def fetch_review(body: FetchReviewRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Fetch review from Google Business Profile API."""
    settings = _settings(request)

    if settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GOOGLE_BUSINESS_ACCOUNT_ID and settings.GOOGLE_BUSINESS_LOCATION_ID:
        try:
            from ..google import GoogleBusinessClient
            gbp = GoogleBusinessClient(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
            review = await _run_external_sync_call(
                operation="fetch-review",
                func=lambda: gbp.get_review(
                    account_id=settings.GOOGLE_BUSINESS_ACCOUNT_ID,
                    location_id=settings.GOOGLE_BUSINESS_LOCATION_ID,
                    review_id=body.review_id,
                ),
                module_roots={"google"},
            )
            return {
                "review_id": body.review_id,
                "rating": review.get("starRating", "FIVE"),
                "content": review.get("comment", ""),
                "reviewer_name": review.get("reviewer", {}).get("displayName", "顧客"),
                "created_at": review.get("createTime", ""),
            }
        except RecoverableToolError as exc:
            logger.warning("Google review fetch failed, using stub: %s", exc)

    return {
        "review_id": body.review_id,
        "rating": 4,
        "content": "東西很好吃！服務也很親切，下次還會再來。",
        "reviewer_name": "顧客",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/generate-review-reply")
async def generate_review_reply(body: GenerateReviewReplyRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Use LiteLLM to generate a personalised review reply."""
    settings = _settings(request)
    review = body.review or {}
    context = body.context or {}
    sentiment = body.sentiment or {}

    brand_name = context.get("brand_name", "")
    brand_tone = context.get("brand_tone", "親切真誠")
    rating = review.get("rating", 4)
    content = review.get("content", "")
    reviewer_name = review.get("reviewer_name", "顧客")
    sentiment_label = sentiment.get("sentiment", "")
    strategy = sentiment.get("recommended_strategy", "")

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            rating_str = str(rating).replace("FIVE", "5").replace("FOUR", "4").replace("THREE", "3").replace("TWO", "2").replace("ONE", "1")
            sentiment_hint = f"情緒：{sentiment_label}，建議策略：{strategy}" if sentiment_label else (
                "正面" if int(rating_str or "4") >= 4 else "負面/中性"
            )
            prompt = (
                f"你是 {brand_name or '這家店'} 的老闆，風格「{brand_tone}」。\n"
                f"請為以下評論撰寫回覆（100字以內，繁體中文，親切自然）：\n\n"
                f"評論者：{reviewer_name}\n星級：{rating} 星\n{sentiment_hint}\n評論內容：{content or '（無文字評論）'}\n\n"
                "要求：不要使用模板語氣，要真誠，若有負評要誠懇回應並承諾改善。"
            )
            reply = await _llm(prompt=prompt, model=settings.LITELLM_MODEL, api_key=settings.GOOGLE_AI_API_KEY, openai_api_key=settings.OPENAI_API_KEY, run_id=body.run_id, generation_name="generate-review-reply")
            return {"reply_draft": reply.strip(), "tone": brand_tone, "confidence": 0.9}
        except RecoverableToolError as exc:
            logger.warning("LLM review reply failed, using stub: %s", exc)

    return {
        "reply_draft": f"感謝 {reviewer_name} 的支持！您的回饋是我們進步的動力，期待再次為您服務 😊",
        "tone": brand_tone,
        "confidence": 0.75,
    }


@router.post("/post-review-reply")
async def post_review_reply(body: PostReviewReplyRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Post reply to Google Business Profile."""
    settings = _settings(request)
    reply_text: str = ""
    if isinstance(body.reply, dict):
        reply_text = body.reply.get("reply_draft") or body.reply.get("text", "")
    if not reply_text:
        reply_text = body.confirmation.get("edited_reply", "")

    if reply_text and settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GOOGLE_BUSINESS_ACCOUNT_ID and settings.GOOGLE_BUSINESS_LOCATION_ID:
        try:
            from ..google import GoogleBusinessClient
            gbp = GoogleBusinessClient(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
            result = await _run_external_sync_call(
                operation="post-review-reply",
                func=lambda: gbp.post_reply(
                    account_id=settings.GOOGLE_BUSINESS_ACCOUNT_ID,
                    location_id=settings.GOOGLE_BUSINESS_LOCATION_ID,
                    review_id=body.review_id,
                    reply_text=reply_text,
                ),
                module_roots={"google"},
            )
            return {"status": "posted", "review_id": body.review_id, "reply": result}
        except RecoverableToolError as exc:
            logger.error("Google review reply post failed: %s", exc)
            return {"status": "failed", "error": str(exc), "review_id": body.review_id}

    logger.info("[Phase 1] Review reply recorded (Google credentials not configured): review_id=%s", body.review_id)
    return {"status": "recorded", "review_id": body.review_id}


@router.post("/analyze-sentiment")
async def analyze_sentiment(body: AnalyzeSentimentRequest, request: Request) -> dict[str, Any]:
    """Review reply step 2: Analyse review sentiment and recommend reply strategy."""
    settings = _settings(request)
    review = body.review or {}
    rating = review.get("rating", 4)
    content = review.get("content", "")

    rating_num: int
    try:
        rating_num = int(str(rating).replace("FIVE", "5").replace("FOUR", "4").replace("THREE", "3").replace("TWO", "2").replace("ONE", "1") or "4")
    except ValueError:
        rating_num = 4

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        prompt = (
            "請分析以下顧客評論的情緒並給出回覆策略建議（繁體中文，只回覆 JSON）：\n\n"
            f"星級：{rating}\n評論：{content or '（無文字評論）'}\n\n"
            "欄位：\n"
            "  sentiment: positive | negative | neutral | mixed\n"
            "  topics: 評論主題關鍵字（字串，逗號分隔）\n"
            "  recommended_strategy: 建議回覆策略（一句話）\n"
            "  tone_guidance: 語氣建議（例如：誠懇感謝、積極改善、低調安撫）\n"
            "  confidence: 0.0-1.0"
        )
        try:
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="analyze-sentiment",
            )
            return _parse_llm_json(raw, operation="analyze-sentiment")
        except RecoverableToolError as exc:
            logger.warning("Sentiment analysis LLM failed, using heuristic: %s", exc)

    # Heuristic fallback
    if rating_num >= 4:
        return {"sentiment": "positive", "topics": "服務,品質", "recommended_strategy": "感謝並邀請再次光臨", "tone_guidance": "誠懇感謝", "confidence": 0.7}
    elif rating_num <= 2:
        return {"sentiment": "negative", "topics": "改善,體驗", "recommended_strategy": "誠懇道歉並承諾改善", "tone_guidance": "積極改善", "confidence": 0.7}
    return {"sentiment": "neutral", "topics": "一般", "recommended_strategy": "感謝並說明特色", "tone_guidance": "親切自然", "confidence": 0.6}


# ── LINE FAQ Workflow tools ───────────────────────────────────────────────────


@router.post("/classify-message")
async def classify_message(body: ClassifyMessageRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Use LiteLLM to classify customer message."""
    settings = _settings(request)

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        prompt = (
            "請判斷以下顧客訊息的類型（繁體中文，只回覆 JSON）：\n"
            f"訊息：{body.message}\n\n"
            "分類選項：faq（常見問題）、complaint（投訴）、order（訂購/預約）、general（一般閒聊）\n"
            "欄位：category, confidence(0.0-1.0), is_answerable(bool), suggested_topic"
        )
        try:
            raw = await _llm(prompt=prompt, model=settings.LITELLM_MODEL, api_key=settings.GOOGLE_AI_API_KEY, openai_api_key=settings.OPENAI_API_KEY, run_id=body.run_id, generation_name="classify-message")
            return _parse_llm_json(raw, operation="classify-message")
        except RecoverableToolError as exc:
            logger.warning("Message classification failed, using stub: %s", exc)

    return {"category": "faq", "confidence": 0.7, "is_answerable": True, "suggested_topic": "一般詢問"}


@router.post("/retrieve-answer")
async def retrieve_answer(body: RetrieveAnswerRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Search knowledge_entries then use LLM to formulate reply."""
    settings = _settings(request)
    repo = _repo(request)

    entries = repo.get_knowledge_entries(body.tenant_id)
    knowledge_text = "\n".join(f"- [{e.category}] {e.content}" for e in entries)

    if not knowledge_text:
        return {"answer": "", "confidence": 0.0, "should_escalate": True, "escalate_reason": "知識庫尚無資料"}

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        prompt = (
            "根據以下品牌知識庫，用繁體中文簡短回答顧客問題（80字以內）。\n"
            "若知識庫中沒有相關資訊，請設 should_escalate: true。\n\n"
            f"知識庫：\n{knowledge_text}\n\n顧客問題：{body.message}\n\n"
            "回覆 JSON：answer, confidence(0.0-1.0), should_escalate(bool), escalate_reason(若升級時填寫)"
        )
        try:
            raw = await _llm(prompt=prompt, model=settings.LITELLM_MODEL, api_key=settings.GOOGLE_AI_API_KEY, openai_api_key=settings.OPENAI_API_KEY, run_id=body.run_id, generation_name="retrieve-answer")
            return _parse_llm_json(raw, operation="retrieve-answer")
        except RecoverableToolError as exc:
            logger.warning("Answer retrieval failed, using stub: %s", exc)

    return {"answer": "感謝您的詢問，我們會盡快回覆您。", "confidence": 0.5, "should_escalate": True, "escalate_reason": "LLM unavailable"}


@router.post("/generate-response")
async def generate_response(body: GenerateResponseRequest, request: Request) -> dict[str, Any]:
    """LINE FAQ step 3: Generate brand-voice final response text from retrieved answer."""
    settings = _settings(request)
    answer = body.answer or {}
    classification = body.classification or {}

    should_escalate: bool = answer.get("should_escalate", False)
    raw_answer: str = answer.get("answer", "")
    escalate_reason: str = answer.get("escalate_reason", "")
    category = classification.get("category", "faq")

    if should_escalate:
        return {
            "response_text": "",
            "should_escalate": True,
            "escalate_reason": escalate_reason or "需要人工協助",
        }

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        prompt = (
            "請將以下原始答案改寫成友善、品牌化的 LINE 回覆訊息（繁體中文，80字以內，不使用過度正式語氣）：\n\n"
            f"顧客問題類型：{category}\n"
            f"顧客問題：{body.message}\n"
            f"原始答案：{raw_answer or '（無資料）'}\n\n"
            "只回覆最終訊息文字，不要其他說明。"
        )
        try:
            response_text = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="generate-response",
            )
            return {
                "response_text": response_text.strip(),
                "should_escalate": False,
                "escalate_reason": "",
            }
        except RecoverableToolError as exc:
            logger.warning("Generate response LLM failed, using raw answer: %s", exc)

    return {
        "response_text": raw_answer or "感謝您的詢問，我們會盡快回覆您！",
        "should_escalate": False,
        "escalate_reason": "",
    }


@router.post("/send-or-escalate")
async def send_or_escalate(body: SendOrEscalateRequest, request: Request) -> dict[str, Any]:
    """Phase 1: Send LINE reply to customer or escalate to boss."""
    settings = _settings(request)
    answer = body.answer or {}
    should_escalate: bool = answer.get("should_escalate", False)
    reply_text: str = answer.get("response_text") or answer.get("answer", "")

    if not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("LINE token not configured; skipping send-or-escalate")
        return {"action": "skipped", "reason": "LINE token not configured"}

    if should_escalate:
        # Auto-reply to customer: acknowledge receipt
        if reply_text or body.customer_line_id:
            try:
                auto_ack = "感謝您的詢問！我們已收到您的留言，將盡快為您回覆 🙏"
                await push_line_messages(
                    to=body.customer_line_id,
                    messages=[text_message(auto_ack)],
                    access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
                )
            except httpx.HTTPError as exc:
                logger.warning("Could not send customer auto-ack on escalation: %s", exc)

        if settings.LINE_BOSS_USER_ID:
            escalate_reason = answer.get("escalate_reason", "顧客問題需要人工回覆")
            boss_text = f"⚠️ 顧客詢問需要你親自回覆：\n\n顧客 LINE ID：{body.customer_line_id}\n原因：{escalate_reason}"
            repo = _repo(request)
            if not repo.can_push(body.tenant_id):
                logger.warning(
                    "send_or_escalate: daily push limit reached for boss; skipping escalation"
                )
                return {"action": "escalation_rate_limited", "customer_line_id": body.customer_line_id}
            try:
                await push_line_messages(to=settings.LINE_BOSS_USER_ID, messages=[text_message(boss_text)], access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
                repo.record_push(
                    tenant_id=body.tenant_id,
                    recipient_line_id=settings.LINE_BOSS_USER_ID,
                    message_type="escalation",
                )
                repo.save_audit_event(
                    tenant_id=body.tenant_id,
                    workflow_type="line_faq",
                    event_type="push_sent",
                    source="send_or_escalate",
                    payload={"message_type": "escalation", "recipient_line_id": settings.LINE_BOSS_USER_ID},
                )
            except httpx.HTTPError as exc:
                logger.error("Failed to escalate to boss: %s", exc)
                repo.save_audit_event(
                    tenant_id=body.tenant_id,
                    workflow_type="line_faq",
                    event_type="push_failed",
                    source="send_or_escalate",
                    payload={"message_type": "escalation", "error": str(exc)},
                )
        return {"action": "escalated", "customer_line_id": body.customer_line_id}

    if reply_text:
        try:
            await push_line_messages(to=body.customer_line_id, messages=[text_message(reply_text)], access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)
            return {"action": "sent", "customer_line_id": body.customer_line_id}
        except httpx.HTTPError as exc:
            logger.error("Failed to send FAQ reply: %s", exc)
            return {"action": "failed", "error": str(exc)}

    return {"action": "skipped", "reason": "no answer text"}


# ════════════════════════════════════════════════════════════════════════════
# Phase 2 Tool Endpoints
# ════════════════════════════════════════════════════════════════════════════

# ── Knowledge Update Workflow tools ──────────────────────────────────────────


@router.post("/parse-knowledge-update")
async def parse_knowledge_update(
    body: ParseKnowledgeUpdateRequest, request: Request
) -> dict[str, Any]:
    """
    Parse boss message to extract: what to change, new value, affected category.
    Returns structured update intent for diff-knowledge step.
    """
    settings = _settings(request)
    boss_msg = body.boss_message

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            prompt = (
                "你是一個知識庫管理員。老闆傳來的訊息如下：\n"
                f"「{boss_msg}」\n\n"
                "請萃取出知識更新意圖，以 JSON 格式回覆，欄位：\n"
                "- update_type: add | modify | delete\n"
                "- category: product | price | contact | style | core_value | pain_point | goal | basic_info\n"
                "- subject: 要更新的具體項目（簡短描述）\n"
                "- old_value: 舊值（不確定則填 null）\n"
                "- new_value: 新值\n"
                "- keywords: 用於搜尋現有條目的關鍵詞列表（list of str）\n"
                "只回傳 JSON，不要加任何說明。"
            )
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="parse-knowledge-update",
            )
            parsed = _parse_llm_json(raw, operation="parse-knowledge-update")
            return {
                "run_id": body.run_id,
                "boss_message": boss_msg,
                "parsed_update": parsed,
            }
        except RecoverableToolError as exc:
            logger.warning("parse-knowledge-update LLM failed, using fallback: %s", exc)

    # Keyword-only fallback
    return {
        "run_id": body.run_id,
        "boss_message": boss_msg,
        "parsed_update": {
            "update_type": "modify",
            "category": "product",
            "subject": boss_msg[:50],
            "old_value": None,
            "new_value": boss_msg,
            "keywords": boss_msg.split()[:5],
        },
    }


@router.post("/diff-knowledge")
async def diff_knowledge(
    body: DiffKnowledgeRequest, request: Request
) -> dict[str, Any]:
    """
    Find existing knowledge entries that conflict/overlap with the parsed update.
    Returns diff summary for the boss to confirm.
    """
    repo = _repo(request)
    parsed = body.parsed_update
    keywords: list[str] = parsed.get("keywords", [])
    categories = [parsed.get("category")] if parsed.get("category") else None

    conflicting = []
    if keywords:
        matched = repo.search_knowledge_entries_by_keywords(
            tenant_id=body.tenant_id,
            keywords=keywords,
            categories=categories,
            limit=5,
        )
        conflicting = [
            {"entry_id": e.id, "category": e.category, "content": e.content[:200]}
            for e in matched
        ]

    return {
        "run_id": body.run_id,
        "parsed_update": parsed,
        "conflicting_entries": conflicting,
        "diff_summary": (
            f"找到 {len(conflicting)} 條可能需要更新的知識條目。"
            if conflicting
            else "知識庫中沒有找到相關的既有條目，將新增一條。"
        ),
    }


@router.post("/apply-knowledge-update")
async def apply_knowledge_update(
    body: ApplyKnowledgeUpdateRequest, request: Request
) -> dict[str, Any]:
    """
    Apply confirmed knowledge update: supersede old entries + create new one.
    """
    repo = _repo(request)
    diff = body.diff
    parsed = diff.get("parsed_update", {})
    conflicting = diff.get("conflicting_entries", [])
    new_content = parsed.get("new_value") or parsed.get("boss_message", "")
    category = parsed.get("category", "product")

    superseded_ids = []
    for entry in conflicting:
        entry_id = entry.get("entry_id")
        if entry_id:
            # Mark each conflicting entry as superseded — do NOT create a new entry here
            repo.mark_knowledge_entry_superseded(entry_id)
            superseded_ids.append(entry_id)

    # Always create exactly one new entry (regardless of how many conflicts were superseded)
    _ = repo.save_knowledge_entry(
        tenant_id=body.tenant_id,
        category=category,
        content=new_content,
        source_type="boss_update",
    )

    logger.info(
        "apply-knowledge-update tenant=%s superseded=%s new_content=%s",
        body.tenant_id,
        superseded_ids,
        new_content[:60],
    )
    return {
        "status": "applied",
        "run_id": body.run_id,
        "superseded_entry_ids": superseded_ids,
        "new_content": new_content,
        "category": category,
    }


# ── Google Post Workflow tools ────────────────────────────────────────────────


@router.post("/determine-post-type")
async def determine_post_type(
    body: DeterminePostTypeRequest, request: Request
) -> dict[str, Any]:
    """
    Determine the optimal Google Business post type (STANDARD / EVENT / OFFER)
    for the given topic, using the boss's recent preferences or LLM reasoning.
    """
    settings = _settings(request)
    topic = body.topic.strip()

    # Keyword heuristics (fast path)
    topic_lower = topic.lower()
    if any(kw in topic_lower for kw in ("活動", "event", "節慶", "紀念日", "慶典", "周年")):
        suggested = "EVENT"
        reason = "主題含活動/節慶關鍵詞"
    elif any(kw in topic_lower for kw in ("優惠", "折扣", "特價", "促銷", "offer", "限時", "買一送一")):
        suggested = "OFFER"
        reason = "主題含優惠/促銷關鍵詞"
    else:
        suggested = "STANDARD"
        reason = "一般資訊動態"

    # If LLM available, refine the decision
    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            prompt = (
                "根據以下主題，判斷最合適的 Google 商家動態類型並說明原因。\n"
                f"主題：{topic}\n\n"
                "可選類型：\n"
                "- STANDARD：一般資訊動態（適合品牌介紹、產品上新、日常分享）\n"
                "- EVENT：活動動態（適合有明確日期的活動、節慶、周年慶）\n"
                "- OFFER：優惠動態（適合折扣、限時促銷、特別優惠）\n\n"
                "只回傳 JSON：{\"post_type\": \"STANDARD|EVENT|OFFER\", \"reason\": \"...\"}"
            )
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="determine-post-type",
            )
            parsed = _parse_llm_json(raw, operation="determine-post-type")
            suggested = parsed.get("post_type", suggested)
            reason = parsed.get("reason", reason)
        except RecoverableToolError as exc:
            logger.warning("determine-post-type LLM failed, using heuristic: %s", exc)

    return {
        "run_id": body.run_id,
        "post_type": suggested,
        "reason": reason,
        "topic": topic,
    }


@router.post("/generate-google-post")
async def generate_google_post(
    body: GenerateGooglePostRequest, request: Request
) -> dict[str, Any]:
    """Generate a Google Business post text for a given topic."""
    settings = _settings(request)
    context = body.context or {}
    brand_name = context.get("brand_name", "")
    brand_tone = context.get("brand_tone", "親切真誠")
    brand_address = context.get("brand_address", "")
    core_values = context.get("core_values", [])

    if not brand_name:
        repo = _repo(request)
        tenant = repo.get_or_create_tenant(body.tenant_id)
        brand_name = tenant.name
        brand_address = tenant.address

    core_values_str = "、".join(core_values) if core_values else ""
    topic_hint = f"主題：{body.topic}\n" if body.topic else ""

    system_prompt = (
        f"你是 {brand_name or '這家店'} 的行銷助理，風格「{brand_tone}」。\n"
        "請撰寫 Google 商家動態（150字以內，正式商業風格，不用 emoji）。\n"
    )
    prompt = (
        f"{topic_hint}"
        f"品牌：{brand_name}，地址：{brand_address}\n"
        f"核心價值：{core_values_str or '用心服務'}\n"
        f"動態類型：{body.post_type}\n\n"
        "要求：第一句是主標題，內容自然有說服力，結尾附上聯絡方式或地址。"
    )

    # Inject preference examples
    memory = _memory(request)
    google_prefs = memory.get_preference_examples(body.tenant_id, "google", limit=2)
    if google_prefs:
        prompt += "\n\n【參考：老闆過去修改的風格】\n"
        for p in google_prefs:
            prompt += f"原版：{p['original'][:100]}\n老闆改為：{p['edited'][:100]}\n---\n"

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            post_text = await _llm(
                prompt=prompt,
                system=system_prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="generate-google-post",
            )
            return {
                "post_text": post_text.strip(),
                "post_type": body.post_type,
                "topic": body.topic,
            }
        except RecoverableToolError as exc:
            logger.warning("generate-google-post LLM failed: %s", exc)

    # Fallback stub
    return {
        "post_text": f"【{brand_name}】{body.topic or '最新消息'}\n\n歡迎蒞臨，用心為您服務。\n📍 {brand_address}",
        "post_type": body.post_type,
        "topic": body.topic,
    }


@router.post("/publish-google-post")
async def publish_google_post(
    body: PublishGooglePostRequest, request: Request
) -> dict[str, Any]:
    """Publish a pre-approved post to Google Business Profile."""
    repo = _repo(request)
    settings = _settings(request)

    if not body.post_text:
        return {"status": "skipped", "reason": "empty post text"}

    # Try OAuth token first (Phase 2), fall back to service account (Phase 1)
    connector = repo.get_connector_account(body.tenant_id, "google_business")
    published = False

    if connector:
        try:
            creds = json.loads(connector.credentials_encrypted)
            access_token = creds.get("access_token", "")
            from ..google.business_client import _GBP_BASE

            post_body: dict[str, Any] = {
                "languageCode": "zh-TW",
                "summary": body.post_text,
                "topicType": body.post_type,
            }
            if body.call_to_action_url:
                post_body["callToAction"] = {"actionType": "LEARN_MORE", "url": body.call_to_action_url}

            # GBP requires account_id/location_id — fall through to service account path
            # if not stored in connector. Use settings as fallback.
            account_id = settings.GOOGLE_BUSINESS_ACCOUNT_ID
            location_id = settings.GOOGLE_BUSINESS_LOCATION_ID
            if account_id and location_id and access_token:
                url = f"{_GBP_BASE}/{account_id}/{location_id}/localPosts"
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
                        content=json.dumps(post_body, ensure_ascii=False).encode(),
                        timeout=20.0,
                    )
                    resp.raise_for_status()
                    gbp_result = resp.json()
                published = True
                repo.decide_pending_approval(agentos_run_id=body.run_id, decision="published", actor_line_id="system")
                wf = repo.get_workflow_record_by_run_id(body.run_id)
                if wf:
                    repo.update_workflow_record_status(wf.id, "completed")
                return {"status": "published", "post_name": gbp_result.get("name"), "via": "oauth"}
        except (httpx.HTTPError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("OAuth GBP publish failed, trying service account: %s", exc)

    if not published and settings.GOOGLE_SERVICE_ACCOUNT_JSON and settings.GOOGLE_BUSINESS_ACCOUNT_ID:
        try:
            from ..google import GoogleBusinessClient
            gbp = GoogleBusinessClient(settings.GOOGLE_SERVICE_ACCOUNT_JSON)
            gbp_result = await _run_external_sync_call(
                operation="publish-google-post/service-account",
                func=lambda: gbp.create_local_post(
                    account_id=settings.GOOGLE_BUSINESS_ACCOUNT_ID,
                    location_id=settings.GOOGLE_BUSINESS_LOCATION_ID,
                    summary=body.post_text,
                    call_to_action_url=body.call_to_action_url,
                ),
                module_roots={"google"},
            )
            repo.decide_pending_approval(agentos_run_id=body.run_id, decision="published", actor_line_id="system")
            wf = repo.get_workflow_record_by_run_id(body.run_id)
            if wf:
                repo.update_workflow_record_status(wf.id, "completed")
            return {"status": "published", "post_name": gbp_result.get("name"), "via": "service_account"}
        except RecoverableToolError as exc:
            logger.error("Service account GBP publish failed: %s", exc)
            return {"status": "failed", "error": str(exc)}

    return {"status": "skipped", "reason": "GBP credentials not configured"}


# ── GA4 Report Workflow tools ─────────────────────────────────────────────────


@router.post("/fetch-ga4-data")
async def fetch_ga4_data(
    body: FetchGA4DataRequest, request: Request
) -> dict[str, Any]:
    """Fetch GA4 metrics for the specified period."""
    repo = _repo(request)
    settings = _settings(request)

    connector = repo.get_connector_account(body.tenant_id, "ga4")
    property_id = settings.GA4_PROPERTY_ID

    if not property_id:
        return {
            "run_id": body.run_id,
            "period": body.period,
            "data": None,
            "note": "GA4_PROPERTY_ID not configured; returning stub data",
            "totals": {"sessions": 0, "totalUsers": 0, "screenPageViews": 0},
        }

    access_token = ""
    if connector:
        try:
            creds = json.loads(connector.credentials_encrypted)
            access_token = creds.get("access_token", "")
        except (json.JSONDecodeError, TypeError):
            pass

    if not access_token:
        return {
            "run_id": body.run_id,
            "period": body.period,
            "data": None,
            "note": "GA4 not connected; use /auth/google/connect to authorise",
            "totals": {"sessions": 0, "totalUsers": 0, "screenPageViews": 0},
        }

    try:
        from ..google import GA4Client
        ga4 = GA4Client(access_token)
        raw = await _run_external_sync_call(
            operation="fetch-ga4-data",
            func=lambda: ga4.run_report(
                property_id=property_id,
                start_date=body.period,
                end_date="today",
            ),
            module_roots={"google"},
        )
        summary = GA4Client.parse_report(raw)
        return {
            "run_id": body.run_id,
            "period": body.period,
            "property_id": property_id,
            "data": summary,
            "totals": summary.get("totals", {}),
        }
    except RecoverableToolError as exc:
        logger.error("GA4 fetch failed: %s", exc)
        return {
            "run_id": body.run_id,
            "period": body.period,
            "data": None,
            "error": str(exc),
            "totals": {"sessions": 0, "totalUsers": 0, "screenPageViews": 0},
        }


@router.post("/generate-ga4-insights")
async def generate_ga4_insights(
    body: GenerateGA4InsightsRequest, request: Request
) -> dict[str, Any]:
    """Generate human-readable GA4 report + action suggestions using LLM."""
    settings = _settings(request)
    totals = body.ga4_data.get("totals", {})
    period = body.ga4_data.get("period", "7天")

    sessions = totals.get("sessions", 0)
    users = totals.get("totalUsers", 0)
    pageviews = totals.get("screenPageViews", 0)
    bounce = totals.get("bounceRate", 0)

    # Format data summary for LLM
    data_summary = (
        f"時段：過去 {period}\n"
        f"工作階段（Sessions）：{int(sessions)}\n"
        f"使用者（Users）：{int(users)}\n"
        f"頁面瀏覽（Page Views）：{int(pageviews)}\n"
        f"跳出率（Bounce Rate）：{float(bounce):.1%}\n"
    )

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            prompt = (
                "你是一位數位行銷顧問，請幫老闆解讀以下 GA4 數據，用繁體中文、人話撰寫：\n\n"
                f"{data_summary}\n\n"
                "請給出：\n"
                "1. 一句話摘要（20字以內）\n"
                "2. 三個亮點或觀察\n"
                "3. 兩個快速行動建議\n\n"
                "以 JSON 格式回覆，欄位：summary, highlights（list）, actions（list）。只回傳 JSON。"
            )
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="generate-ga4-insights",
            )
            insights = _parse_llm_json(raw, operation="generate-ga4-insights")
            return {"run_id": body.run_id, "insights": insights, "raw_data_summary": data_summary}
        except RecoverableToolError as exc:
            logger.warning("GA4 insights LLM failed: %s", exc)

    # Fallback
    return {
        "run_id": body.run_id,
        "insights": {
            "summary": f"本週網站有 {int(users)} 位使用者",
            "highlights": [
                f"工作階段：{int(sessions)}",
                f"頁面瀏覽：{int(pageviews)}",
                f"跳出率：{float(bounce):.1%}",
            ],
            "actions": ["持續更新 Google 商家資訊", "考慮在 LINE 分享更多優惠訊息"],
        },
        "raw_data_summary": data_summary,
    }


@router.post("/generate-recommendations")
async def generate_recommendations(
    body: GenerateRecommendationsRequest, request: Request
) -> dict[str, Any]:
    """
    Generate standalone prioritised action recommendations based on GA4 data + insights.
    Produces a richer set of recommendations than the quick actions in generate-ga4-insights.
    """
    settings = _settings(request)
    insights = body.insights.get("insights", {})
    totals = body.ga4_data.get("totals", {})

    base_actions = insights.get("actions", [])
    period = body.ga4_data.get("period", "7天")

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        try:
            highlights_str = "\n".join(f"- {h}" for h in insights.get("highlights", []))
            data_summary = (
                f"時段：過去 {period}\n"
                f"工作階段：{int(totals.get('sessions', 0))}\n"
                f"使用者：{int(totals.get('totalUsers', 0))}\n"
                f"跳出率：{float(totals.get('bounceRate', 0)):.1%}\n\n"
                f"摘要：{insights.get('summary', '')}\n"
                f"觀察：\n{highlights_str}"
            )
            prompt = (
                "你是數位行銷顧問，根據以下 GA4 數據與分析，提供具體可執行的改善建議。\n\n"
                f"{data_summary}\n\n"
                "請提供 3-5 個優先行動方案，每個方案包含：\n"
                "- title：行動標題（15字以內）\n"
                "- detail：具體說明（50字以內）\n"
                "- priority：high/medium/low\n\n"
                "以 JSON 格式回覆：{\"recommendations\": [{\"title\": ..., \"detail\": ..., \"priority\": ...}]}"
            )
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                run_id=body.run_id,
                generation_name="generate-recommendations",
            )
            parsed = _parse_llm_json(raw, operation="generate-recommendations")
            recommendations = parsed.get("recommendations", [])
            # Phase 5: persist recommendations to SharedContext for cross-workflow use
            try:
                repo = _repo(request)
                repo.save_shared_context(
                    tenant_id=body.tenant_id,
                    context_type="ga4_recommendations",
                    content={"recommendations": recommendations, "period": period},
                    source_run_id=body.run_id,
                    ttl_hours=168,  # 7 days
                )
            except SQLAlchemyError as _ctx_err:
                logger.warning("SharedContext save failed (non-blocking): %s", _ctx_err)
            return {"run_id": body.run_id, "recommendations": recommendations}
        except RecoverableToolError as exc:
            logger.warning("generate-recommendations LLM failed: %s", exc)

    # Fallback from base actions
    return {
        "run_id": body.run_id,
        "recommendations": [
            {"title": a, "detail": "", "priority": "medium"} for a in base_actions
        ],
    }


@router.post("/send-ga4-report")
async def send_ga4_report(
    body: SendGA4ReportRequest, request: Request
) -> dict[str, Any]:
    """Push GA4 weekly report to boss via LINE Flex Message with Google Post CTA button."""
    settings = _settings(request)
    insights = body.insights.get("insights", {})

    if not settings.LINE_BOSS_USER_ID or not settings.LINE_CHANNEL_ACCESS_TOKEN:
        logger.warning("LINE not configured; skipping GA4 report push")
        return {"status": "skipped", "insights": insights}

    repo = _repo(request)
    if not repo.can_push(body.tenant_id):
        logger.warning(
            "send_ga4_report: daily push limit reached for tenant=%s; skipping report",
            body.tenant_id,
        )
        return {"status": "rate_limited", "run_id": body.run_id}

    flex_bubble = build_ga4_report_flex(
        run_id=body.run_id,
        tenant_id=body.tenant_id,
        insights=insights,
    )

    try:
        await push_line_messages(
            to=settings.LINE_BOSS_USER_ID,
            messages=[{"type": "flex", "altText": "📊 本週 GA4 週報已出爐", "contents": flex_bubble}],
            access_token=settings.LINE_CHANNEL_ACCESS_TOKEN,
        )

        repo.record_push(
            tenant_id=body.tenant_id,
            recipient_line_id=settings.LINE_BOSS_USER_ID,
            message_type="report",
        )
        repo.save_audit_event(
            tenant_id=body.tenant_id,
            agentos_run_id=body.run_id,
            workflow_type="ga4_report",
            event_type="push_sent",
            source="send_ga4_report",
            payload={"message_type": "report", "recipient_line_id": settings.LINE_BOSS_USER_ID},
        )

        return {"status": "sent", "run_id": body.run_id}
    except httpx.HTTPError as exc:
        logger.error("GA4 report push failed: %s", exc)
        repo.save_audit_event(
            tenant_id=body.tenant_id,
            agentos_run_id=body.run_id,
            workflow_type="ga4_report",
            event_type="push_failed",
            source="send_ga4_report",
            payload={"message_type": "report", "error": str(exc)},
        )
        return {"status": "failed", "error": str(exc)}
