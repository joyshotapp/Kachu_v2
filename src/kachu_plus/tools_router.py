from __future__ import annotations

import importlib
import json
import sys
from datetime import datetime, timezone
from typing import Any

try:
    import litellm as _litellm_mod
    _LITELLM_AVAILABLE = True
except Exception as _litellm_err:  # noqa: BLE001
    import logging as _logging
    _logging.getLogger(__name__).warning("litellm import failed: %s", _litellm_err)
    _litellm_mod = None  # type: ignore[assignment]
    _LITELLM_AVAILABLE = False

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from kachu_plus.google_business import GoogleBusinessConnectorError, GoogleReviewService, _normalize_review
from kachu_plus.industry_playbook import build_industry_context
from kachu_plus.line.flex_builder import (
    build_external_reply_flex,
    build_photo_content_flex,
    build_google_post_flex,
    build_knowledge_update_flex,
    build_planned_content_flex,
    build_review_reply_flex,
)
from kachu_plus.line.push import push_line_messages, resolve_tenant_line_recipients, text_message
from kachu_plus.learning import ContextBriefManager, MemoryManager, PostTaskReviewService
from kachu_plus.meta import MetaConnectorError, MetaGraphClient, resolve_line_push_access_token
from kachu_plus.publishing import publish_content_bundle, publish_content_bundle_succeeded, publish_review_reply
from kachu_plus.retrieval_plan import RetrievalPlanComposer
from kachu_plus.website_knowledge import select_knowledge_highlights

router = APIRouter(prefix="/tools", tags=["tools"])


class AnalyzeSentimentRequest(BaseModel):
    tenant_id: str
    review: dict[str, Any]
    run_id: str = ""


class FetchReviewRequest(BaseModel):
    tenant_id: str
    review_id: str


class RetrieveContextRequest(BaseModel):
    tenant_id: str
    query: str = ""
    workflow_type: str = ""
    run_id: str = ""


class GenerateReviewReplyRequest(BaseModel):
    tenant_id: str
    review: dict[str, Any] | None = None
    context: dict[str, Any] | None = None
    sentiment: dict[str, Any] | None = None
    run_id: str = ""


class NotifyApprovalRequest(BaseModel):
    tenant_id: str
    run_id: str
    workflow: str
    drafts: dict[str, Any] = Field(default_factory=dict)
    task_id: str = ""
    review_id: str = ""


class DeterminePostTypeRequest(BaseModel):
    tenant_id: str
    topic: str
    run_id: str = ""


class GenerateGooglePostRequest(BaseModel):
    tenant_id: str
    topic: str = ""
    post_type: str = "STANDARD"
    context: dict[str, Any] | None = None
    run_id: str = ""


class PublishGooglePostRequest(BaseModel):
    tenant_id: str
    run_id: str
    post_text: str = ""
    post_type: str = "STANDARD"
    selected_platforms: list[str] = Field(default_factory=lambda: ["google"])
    drafts: dict[str, Any] | None = None
    call_to_action_url: str = ""


class PostReviewReplyRequest(BaseModel):
    tenant_id: str
    run_id: str
    review_id: str
    reply: dict[str, Any] | None = None
    confirmation: dict[str, Any] = Field(default_factory=dict)


class AnalyzePhotoRequest(BaseModel):
    tenant_id: str
    photo_url: str = ""
    line_message_id: str = ""
    run_id: str = ""


class CheckDraftDirectionRequest(BaseModel):
    tenant_id: str
    analysis: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class GenerateContentPlanRequest(BaseModel):
    tenant_id: str
    objective: str = ""
    selected_platforms: list[str] = Field(default_factory=lambda: ["ig_fb", "google"])
    context: dict[str, Any] = Field(default_factory=dict)
    seed_text: str = ""
    analysis: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class CreateContentPlanRequest(BaseModel):
    tenant_id: str
    objective: str
    context: dict[str, Any] = Field(default_factory=dict)
    selected_platforms: list[str] = Field(default_factory=lambda: ["ig_fb", "google"])
    scheduled_for: datetime | None = None
    source_conversation_id: str = ""
    created_by_line_user_id: str = ""


class GenerateDraftsRequest(BaseModel):
    tenant_id: str
    analysis: dict[str, Any] = Field(default_factory=dict)
    context: dict[str, Any] = Field(default_factory=dict)
    content_plan: dict[str, Any] = Field(default_factory=dict)
    workflow_input: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class ParseKnowledgeUpdateRequest(BaseModel):
    tenant_id: str
    boss_message: str
    run_id: str = ""


class DiffKnowledgeRequest(BaseModel):
    tenant_id: str
    parsed_update: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class ApplyKnowledgeUpdateRequest(BaseModel):
    tenant_id: str
    diff: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class ClassifyMessageRequest(BaseModel):
    tenant_id: str
    message: str
    run_id: str = ""


class RetrieveAnswerRequest(BaseModel):
    tenant_id: str
    message: str
    classification: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class GenerateResponseRequest(BaseModel):
    tenant_id: str
    message: str
    classification: dict[str, Any] = Field(default_factory=dict)
    answer: dict[str, Any] = Field(default_factory=dict)
    run_id: str = ""


class SendOrEscalateRequest(BaseModel):
    tenant_id: str
    customer_line_id: str
    message: str = ""
    answer: dict[str, Any] = Field(default_factory=dict)
    recipient_line_ids: list[str] = Field(default_factory=list)
    run_id: str = ""


class PublishContentRequest(BaseModel):
    tenant_id: str
    run_id: str
    drafts: dict[str, Any] = Field(default_factory=dict)
    selected_platforms: list[str] = Field(default_factory=lambda: ["ig_fb", "google"])


def _repo(request: Request):
    return request.app.state.repository


def _settings(request: Request):
    return request.app.state.settings


def _review_service(request: Request) -> GoogleReviewService:
    service = getattr(request.app.state, "google_review_service", None)
    if service is None:
        service = GoogleReviewService(_repo(request), _settings(request))
        request.app.state.google_review_service = service
    return service


def _memory(request: Request) -> MemoryManager:
    service = getattr(request.app.state, "memory_manager", None)
    if service is None:
        service = MemoryManager(_repo(request), _settings(request))
        request.app.state.memory_manager = service
    return service


def _briefs(request: Request) -> ContextBriefManager:
    service = getattr(request.app.state, "context_brief_manager", None)
    if service is None:
        service = ContextBriefManager(_repo(request), _memory(request))
        request.app.state.context_brief_manager = service
    return service


def _post_review(request: Request) -> PostTaskReviewService:
    service = getattr(request.app.state, "post_task_review", None)
    if service is None:
        service = PostTaskReviewService(_repo(request), _memory(request), _briefs(request))
        request.app.state.post_task_review = service
    return service


def _retrieval_plan(request: Request) -> RetrievalPlanComposer:
    service = getattr(request.app.state, "retrieval_plan_composer", None)
    if service is None:
        service = RetrievalPlanComposer(_repo(request), _memory(request))
        request.app.state.retrieval_plan_composer = service
    return service


def _select_llm_api_key(*, model: str, google_api_key: str, openai_api_key: str) -> str:
    if model.startswith("gemini/"):
        return google_api_key
    if model.startswith("gpt") or model.startswith("openai/"):
        return openai_api_key
    return google_api_key or openai_api_key


def _get_litellm_module() -> Any:
    cached = sys.modules.get("litellm")
    if cached is not None:
        return cached
    if _litellm_mod is not None:
        return _litellm_mod
    try:
        return importlib.import_module("litellm")
    except Exception:  # noqa: BLE001
        return None


def _is_hard_fact_faq(*, message: str, best_entry: dict[str, Any]) -> bool:
    category = str(best_entry.get("category") or "")
    if category in {"basic_info", "offer"}:
        return True
    return any(token in message for token in ("幾點", "營業", "地址", "電話", "多少錢", "價格"))


async def _llm(
    *,
    prompt: str,
    model: str,
    api_key: str,
    openai_api_key: str = "",
    image_url: str = "",
) -> str:
    litellm_module = _get_litellm_module()
    if litellm_module is None:
        raise ImportError("litellm not installed")

    user_content: Any = prompt
    if image_url:
        user_content = [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]
    response = await litellm_module.acompletion(
        model=model,
        messages=[
            {"role": "system", "content": "你是 Kachu+ 的商家內容分析助手。若被要求輸出 JSON，就只能輸出 JSON 物件本身。"},
            {"role": "user", "content": user_content},
        ],
        api_key=_select_llm_api_key(
            model=model,
            google_api_key=api_key,
            openai_api_key=openai_api_key,
        ),
    )
    content = response.choices[0].message.content or ""
    return str(content).strip()


def _parse_json_object(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`").strip()
        if cleaned.startswith("json"):
            cleaned = cleaned[4:].strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("json object not found")
    payload = json.loads(cleaned[start:end + 1])
    if not isinstance(payload, dict):
        raise ValueError("response is not a json object")
    return payload


async def classify_customer_message(*, settings: Any, message: str) -> dict[str, Any]:
    lowered = message.lower()
    if any(token in message for token in ("幾點", "營業", "地址", "電話", "多少錢", "價格")):
        return {"category": "faq", "confidence": 0.8, "is_answerable": True, "suggested_topic": "常見問題"}
    if any(token in lowered for token in ("爛", "慢", "不爽", "差", "失望")):
        return {"category": "complaint", "confidence": 0.8, "is_answerable": False, "suggested_topic": "客訴"}
    return {"category": "general", "confidence": 0.6, "is_answerable": True, "suggested_topic": "一般詢問"}


async def retrieve_faq_answer(*, repo: Any, settings: Any, tenant_id: str, message: str) -> dict[str, Any]:
    tenant = repo.get_tenant(tenant_id)
    industry_context = build_industry_context(tenant.industry_type if tenant is not None else "")
    entries = RetrievalPlanComposer(repo, MemoryManager(repo, settings)).compose(
        tenant_id=tenant_id,
        query=message,
        workflow_type="customer_faq",
        platform="line",
    )
    ranked_knowledge = entries.get("persistent_knowledge", [])
    if not ranked_knowledge:
        return {"answer": "", "confidence": 0.0, "should_escalate": True, "escalate_reason": "知識庫尚無資料"}

    best_entry = next((entry for entry in ranked_knowledge if float(entry.get("score", 0.0) or 0.0) > 0.1), None)
    if best_entry is None and any(token in message for token in ("幾點", "營業")):
        best_entry = next((entry for entry in ranked_knowledge if entry.get("category") == "basic_info"), None)
    if best_entry is None:
        return {"answer": "", "confidence": 0.2, "should_escalate": True, "escalate_reason": "知識庫沒有相符內容"}

    answer_text = str(best_entry.get("content", "") or "")
    if (settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY) and not _is_hard_fact_faq(message=message, best_entry=best_entry):
        prompt = (
            "根據以下品牌知識，用繁體中文簡短回答顧客問題，80 字內。"
            f"產業語氣：{industry_context.get('recommended_tone', '親切真誠')}。"
            f"知識：{answer_text}。顧客問題：{message}。"
            "只回覆答案文字。"
        )
        try:
            answer_text = (await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
            )).strip()
        except Exception:
            pass

    confidence = max(0.4, min(0.95, float(best_entry.get("score", 0.8) or 0.8) / 4.0))
    return {
        "answer": answer_text,
        "confidence": confidence,
        "should_escalate": False,
        "escalate_reason": "",
        "retrieval_plan": entries,
    }


async def generate_customer_response(*, answer: dict[str, Any]) -> dict[str, Any]:
    if answer.get("should_escalate"):
        return {
            "response_text": "",
            "should_escalate": True,
            "escalate_reason": answer.get("escalate_reason", "需要人工協助"),
        }
    raw_answer = str(answer.get("answer", "") or "")
    return {"response_text": raw_answer or "感謝您的詢問，我們會盡快回覆您！", "should_escalate": False, "escalate_reason": ""}


async def send_or_escalate_customer_response(
    *,
    repo: Any,
    settings: Any,
    tenant_id: str,
    customer_line_id: str,
    message: str,
    answer: dict[str, Any],
    recipient_line_ids: list[str] | None = None,
    access_token_override: str = "",
) -> dict[str, Any]:
    access_token = access_token_override or resolve_line_push_access_token(repo=repo, settings=settings, tenant_id=tenant_id)
    if not access_token:
        return {"action": "skipped", "reason": "line_channel_access_token_missing"}

    if answer.get("should_escalate"):
        auto_ack = "感謝您的詢問！我們已收到您的留言，將盡快為您回覆。"
        await push_line_messages(to=customer_line_id, messages=[text_message(auto_ack)], access_token=access_token)
        repo.save_conversation(
            tenant_id=tenant_id,
            line_user_id=customer_line_id,
            actor_role="ai",
            channel_type="line",
            conversation_kind="customer_faq",
            content_text=auto_ack,
            metadata={"action": "escalated"},
        )
        recipients = [value for value in (recipient_line_ids or resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id=tenant_id)) if value]
        if recipients:
            reason = str(answer.get("escalate_reason", "顧客問題需要人工回覆") or "顧客問題需要人工回覆")
            boss_text = f"⚠️ 顧客詢問需要你親自回覆：\n\n顧客 LINE ID：{customer_line_id}\n原因：{reason}\n原始訊息：{message}"
            for recipient in recipients:
                await push_line_messages(to=recipient, messages=[text_message(boss_text)], access_token=access_token)
        return {"action": "escalated", "customer_line_id": customer_line_id, "response_text": auto_ack}

    response_text = str(answer.get("response_text") or answer.get("answer") or "")
    await push_line_messages(to=customer_line_id, messages=[text_message(response_text)], access_token=access_token)
    repo.save_conversation(
        tenant_id=tenant_id,
        line_user_id=customer_line_id,
        actor_role="ai",
        channel_type="line",
        conversation_kind="customer_faq",
        content_text=response_text,
        metadata={"action": "sent"},
    )
    return {"action": "sent", "customer_line_id": customer_line_id, "response_text": response_text}


async def run_line_faq_flow(
    *,
    repo: Any,
    settings: Any,
    tenant_id: str,
    customer_line_id: str,
    message: str,
    recipient_line_ids: list[str] | None = None,
    access_token_override: str = "",
) -> dict[str, Any]:
    _ = await classify_customer_message(settings=settings, message=message)
    answer = await retrieve_faq_answer(repo=repo, settings=settings, tenant_id=tenant_id, message=message)
    final_answer = await generate_customer_response(answer=answer)
    return await send_or_escalate_customer_response(
        repo=repo,
        settings=settings,
        tenant_id=tenant_id,
        customer_line_id=customer_line_id,
        message=message,
        answer=final_answer,
        recipient_line_ids=recipient_line_ids,
        access_token_override=access_token_override,
    )


def _infer_knowledge_category(message: str) -> str:
    if any(token in message for token in ("地址", "電話", "營業時間", "公休", "開始營業", "打烊")):
        return "basic_info"
    if any(token in message for token in ("價格", "售價", "元", "優惠", "折扣", "買一送一")):
        return "offer"
    if any(token in message for token in ("口味", "品項", "菜單", "套餐", "便當", "產品", "商品")):
        return "product"
    if any(token in message for token in ("風格", "語氣", "品牌", "形象")):
        return "style"
    if any(token in message for token in ("價值", "理念", "堅持")):
        return "core_value"
    if any(token in message for token in ("困擾", "痛點", "抱怨")):
        return "pain_point"
    if any(token in message for token in ("目標", "希望", "今年")):
        return "goal"
    return "product"


def _infer_update_type(message: str) -> str:
    if any(token in message for token in ("刪除", "取消", "下架", "停售")):
        return "delete"
    if any(token in message for token in ("新增", "推出", "上架", "增加")):
        return "add"
    return "modify"


def _extract_update_values(message: str) -> tuple[str, str | None, str]:
    normalized = message.replace("，", " ").replace("。", " ").strip()
    for separator in ("改成", "改為", "更新為", "調整成", "調整為", "變成", "現在是"):
        if separator in normalized:
            subject, new_value = normalized.split(separator, 1)
            return subject.strip(), None, new_value.strip()
    return normalized[:50], None, normalized


def _build_knowledge_keywords(message: str, subject: str, new_value: str) -> list[str]:
    raw_tokens = [subject, new_value, *message.replace("，", " ").replace("。", " ").split()]
    keywords: list[str] = []
    for token in raw_tokens:
        cleaned = token.strip()
        if len(cleaned) < 2:
            continue
        if cleaned not in keywords:
            keywords.append(cleaned)
        if len(keywords) >= 5:
            break
    return keywords


async def _load_context_briefs(request: Request, tenant_id: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    repo = _repo(request)
    now = datetime.now(timezone.utc)

    def _read_brief(brief_type: str) -> dict[str, Any]:
        entry = repo.get_context_brief(tenant_id, brief_type)
        if entry is None:
            return {}
        expires_at = entry.expires_at
        if expires_at is not None and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at is not None and expires_at < now:
            return {}
        return json.loads(entry.content_json or "{}")

    brand_brief = _read_brief("brand_brief")
    owner_brief = _read_brief("owner_brief")
    customer_brief = _read_brief("customer_brief")
    if not brand_brief or not owner_brief or not customer_brief:
        refreshed = await _briefs(request).refresh_briefs(tenant_id, reason="tool_retrieve_context")
        brand_brief = refreshed.get("brand_brief", brand_brief)
        owner_brief = refreshed.get("owner_brief", owner_brief)
        customer_brief = refreshed.get("customer_brief", customer_brief)
    return brand_brief, owner_brief, customer_brief


def _normalize_selected_platforms(platforms: list[str] | None) -> list[str]:
    normalized: list[str] = []
    for value in platforms or []:
        item = str(value or "").strip().lower()
        if item in {"ig", "fb", "instagram", "facebook", "ig_fb"}:
            item = "ig_fb"
        elif item in {"google", "google_business", "gbp"}:
            item = "google"
        else:
            continue
        if item not in normalized:
            normalized.append(item)
    return normalized or ["ig_fb", "google"]


def _default_call_to_action(industry_type: str) -> str:
    lowered = str(industry_type or "").lower()
    if any(token in lowered for token in ("cafe", "restaurant", "餐", "咖啡")):
        return "歡迎來店或私訊預約。"
    if any(token in lowered for token in ("beauty", "nail", "美", "spa")):
        return "歡迎私訊預約時段。"
    return "歡迎私訊了解更多。"


async def build_content_plan_payload(
    *,
    consultant: Any,
    tenant_name: str,
    industry_type: str,
    objective: str,
    selected_platforms: list[str] | None = None,
    context: dict[str, Any] | None = None,
    seed_text: str = "",
    analysis: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan_context = context or {}
    analysis_payload = analysis or {}
    platforms = _normalize_selected_platforms(selected_platforms)
    industry_context = plan_context.get("industry_context") or build_industry_context(industry_type)
    owner_brief = plan_context.get("owner_brief") or {}
    brand_brief = plan_context.get("brand_brief") or {}
    knowledge = [str(item).strip() for item in (plan_context.get("knowledge") or plan_context.get("relevant_knowledge") or []) if str(item).strip()]
    content_angles = [str(item).strip() for item in industry_context.get("content_angles", []) if str(item).strip()]
    priorities = [str(item).strip() for item in owner_brief.get("current_priorities", []) if str(item).strip()]
    brand_tone = str(plan_context.get("brand_tone") or industry_context.get("recommended_tone") or "親切真誠")
    planning_goal = str(objective or seed_text or analysis_payload.get("scene_description") or "本週內容規劃").strip()
    campaign_angle = content_angles[0] if content_angles else planning_goal
    audience_hint = str(brand_brief.get("target_audience") or owner_brief.get("target_customer") or "正在考慮是否要來店的潛在顧客")
    proof_points = [item for item in [*knowledge[:2], *priorities[:2], *content_angles[1:3]] if item][:3]
    if not proof_points:
        proof_points = ["把這次主題說清楚", "讓顧客知道來店價值", "結尾給明確行動呼籲"]
    call_to_action = str(plan_context.get("call_to_action") or _default_call_to_action(industry_type))

    creative_direction = await consultant.build_reply(
        tenant_name=tenant_name or "你的店",
        industry_type="content_plan",
        message=(
            f"請為 {tenant_name or '你的店'} 規劃一段貼文企劃方向。"
            f"目標：{planning_goal}。品牌語氣：{brand_tone}。核心角度：{campaign_angle}。"
            f"受眾：{audience_hint}。請輸出 60 字內方向摘要，不要加條列。"
        ),
    )
    creative_direction = creative_direction.strip() or f"先用 {campaign_angle} 切入，再把品牌價值與行動呼籲說清楚。"

    platform_briefs: dict[str, str] = {}
    if "ig_fb" in platforms:
        platform_briefs["ig_fb"] = f"用 {brand_tone} 語氣先抓住注意力，再帶出 {campaign_angle}，最後用 CTA 收尾。"
    if "google" in platforms:
        platform_briefs["google"] = f"把 {planning_goal} 說成清楚的商家近況更新，保留實用資訊與 CTA。"

    schedule_window = {
        "recommended_window": "48 小時內發布",
        "reason": "讓企劃方向與近期主題保持一致，避免素材過期。",
    }
    suggested_hashtags = [tag for tag in analysis_payload.get("suggested_tags", []) if str(tag).strip()][:4]
    if not suggested_hashtags and campaign_angle:
        suggested_hashtags = [f"#{campaign_angle.replace(' ', '')[:12]}"]

    return {
        "objective": planning_goal,
        "headline": planning_goal,
        "campaign_angle": campaign_angle,
        "audience_hint": audience_hint,
        "brand_tone": brand_tone,
        "proof_points": proof_points,
        "call_to_action": call_to_action,
        "selected_platforms": platforms,
        "platform_briefs": platform_briefs,
        "creative_direction": creative_direction,
        "schedule_window": schedule_window,
        "suggested_hashtags": suggested_hashtags,
        "direction_prompt": (
            f"企劃目標：{planning_goal}；核心角度：{campaign_angle}；"
            f"品牌語氣：{brand_tone}；佐證重點：{'、'.join(proof_points)}；CTA：{call_to_action}"
        ),
    }


async def build_content_drafts(
    *,
    consultant: Any,
    context: dict[str, Any],
    analysis: dict[str, Any],
    content_plan: dict[str, Any] | None = None,
) -> dict[str, Any]:
    plan = content_plan or {}
    brand_name = str(context.get("brand_name", "你的店") or "你的店")
    brand_tone = str(plan.get("brand_tone") or context.get("brand_tone", "親切真誠") or "親切真誠")
    scene = str(plan.get("headline") or analysis.get("scene_description") or "今日分享")
    campaign_angle = str(plan.get("campaign_angle") or scene)
    creative_direction = str(plan.get("creative_direction") or plan.get("direction_prompt") or "")
    platform_briefs = plan.get("platform_briefs") or {}
    call_to_action = str(plan.get("call_to_action") or "歡迎私訊了解更多。")
    tags = " ".join((plan.get("suggested_hashtags") or analysis.get("suggested_tags") or [])[:4])
    ig_prompt = (
        f"請為 {brand_name} 用 {brand_tone} 語氣寫一篇 IG/FB 貼文。"
        f"主題：{scene}。企劃角度：{campaign_angle}。平台方向：{platform_briefs.get('ig_fb', creative_direction)}。"
        f"結尾 CTA：{call_to_action}。附上 2-4 個 hashtag：{tags}。200 字內。"
    )
    google_prompt = (
        f"請為 {brand_name} 寫一則 Google 商家動態。"
        f"主題：{scene}。企劃角度：{campaign_angle}。平台方向：{platform_briefs.get('google', creative_direction)}。"
        f"結尾 CTA：{call_to_action}。150 字內，繁體中文。"
    )
    ig_fb = await consultant.build_reply(tenant_name=brand_name, industry_type="photo_content", message=ig_prompt)
    google = await consultant.build_reply(tenant_name=brand_name, industry_type="photo_content", message=google_prompt)
    return {
        "ig_fb": ig_fb,
        "google": google,
        "selected_platforms": plan.get("selected_platforms") or ["ig_fb", "google"],
        "content_plan_summary": {
            "objective": plan.get("objective") or scene,
            "campaign_angle": campaign_angle,
            "call_to_action": call_to_action,
        },
    }


@router.post("/fetch-review")
def fetch_review(body: FetchReviewRequest, request: Request) -> dict[str, Any]:
    try:
        review = _review_service(request).fetch_review(body.tenant_id, review_id=body.review_id)
        return _normalize_review(review)
    except GoogleBusinessConnectorError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/analyze-photo")
async def analyze_photo(body: AnalyzePhotoRequest, request: Request) -> dict[str, Any]:
    return await analyze_photo_payload(
        photo_url=body.photo_url,
        line_message_id=body.line_message_id,
        run_id=body.run_id,
        settings=_settings(request),
    )


async def analyze_photo_payload(
    *,
    photo_url: str,
    line_message_id: str = "",
    run_id: str = "",
    settings: Any,
) -> dict[str, Any]:
    if not photo_url:
        return {
            "analysis_id": f"analysis-{line_message_id or run_id}",
            "scene_description": "照片未提供，請重新上傳後再試一次。",
            "upload_intent": "日常分享",
            "detected_objects": [],
            "suggested_tags": ["#新品", "#店家日常"],
            "quality_score": 0.0,
            "status": "needs_manual_review",
            "needs_manual_review": True,
        }

    if settings.GOOGLE_AI_API_KEY or settings.OPENAI_API_KEY:
        prompt = (
            "請分析這張商家上傳照片，並只輸出 JSON。"
            'JSON schema: {"scene_description": str, "upload_intent": str, "detected_objects": [str], '
            '"suggested_tags": [str], "quality_score": 0~1 number, "needs_manual_review": bool}. '
            "請用繁體中文，scene_description 要具體、能直接拿去寫貼文。"
        )
        try:
            raw = await _llm(
                prompt=prompt,
                model=settings.LITELLM_MODEL,
                api_key=settings.GOOGLE_AI_API_KEY,
                openai_api_key=settings.OPENAI_API_KEY,
                image_url=photo_url,
            )
            payload = _parse_json_object(raw)
            quality_score = max(0.0, min(1.0, float(payload.get("quality_score", 0.0))))
            detected_objects = [str(item).strip() for item in payload.get("detected_objects", []) if str(item).strip()]
            suggested_tags = [str(item).strip() for item in payload.get("suggested_tags", []) if str(item).strip()]
            return {
                "analysis_id": f"analysis-{line_message_id or run_id}",
                "scene_description": str(payload.get("scene_description") or "老闆剛上傳一張可用於社群貼文的照片。"),
                "upload_intent": str(payload.get("upload_intent") or "日常分享"),
                "detected_objects": detected_objects,
                "suggested_tags": suggested_tags or ["#品牌日常", "#本週推薦"],
                "quality_score": quality_score,
                "status": "analyzed",
                "needs_manual_review": bool(payload.get("needs_manual_review", quality_score < 0.45)),
            }
        except Exception:  # noqa: BLE001
            pass

    return {
        "analysis_id": f"analysis-{line_message_id or run_id}",
        "scene_description": "老闆剛上傳一張可用於社群貼文的照片。",
        "upload_intent": "日常分享",
        "detected_objects": ["店內商品"],
        "suggested_tags": ["#品牌日常", "#本週推薦"],
        "quality_score": 0.75,
        "status": "analyzed",
        "needs_manual_review": False,
    }


@router.post("/analyze-sentiment")
def analyze_sentiment(body: AnalyzeSentimentRequest) -> dict[str, Any]:
    review = body.review or {}
    rating = str(review.get("rating", "")).upper()
    content = str(review.get("content", ""))
    if any(token in content for token in ("不好", "失望", "差", "慢", "冷掉")) or rating in {"ONE", "TWO"}:
        return {
            "sentiment": "negative",
            "topics": "服務,體驗",
            "recommended_strategy": "先道歉，再承諾改善並邀請私訊補充情況",
            "tone_guidance": "誠懇安撫",
            "confidence": 0.8,
        }
    if rating in {"FOUR", "FIVE"} or any(token in content for token in ("很好", "喜歡", "推薦", "親切")):
        return {
            "sentiment": "positive",
            "topics": "服務,品質",
            "recommended_strategy": "感謝支持並邀請再次光臨",
            "tone_guidance": "真誠感謝",
            "confidence": 0.8,
        }
    return {
        "sentiment": "neutral",
        "topics": "一般",
        "recommended_strategy": "感謝回饋並簡短補充品牌特色",
        "tone_guidance": "親切自然",
        "confidence": 0.6,
    }


@router.post("/classify-message")
async def classify_message(body: ClassifyMessageRequest, request: Request) -> dict[str, Any]:
    return await classify_customer_message(settings=_settings(request), message=body.message)


@router.post("/retrieve-answer")
async def retrieve_answer(body: RetrieveAnswerRequest, request: Request) -> dict[str, Any]:
    return await retrieve_faq_answer(
        repo=_repo(request),
        settings=_settings(request),
        tenant_id=body.tenant_id,
        message=body.message,
    )


@router.post("/generate-response")
async def generate_response(body: GenerateResponseRequest, request: Request) -> dict[str, Any]:
    return await generate_customer_response(answer=body.answer)


@router.post("/retrieve-context")
async def retrieve_context(body: RetrieveContextRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    tenant = repo.get_tenant(body.tenant_id)
    brand_brief, owner_brief, customer_brief = await _load_context_briefs(request, body.tenant_id)
    industry_context = brand_brief.get("industry_context") or build_industry_context(
        tenant.industry_type if tenant is not None else ""
    )
    retrieval_plan = _retrieval_plan(request).compose(
        tenant_id=body.tenant_id,
        query=body.query,
        workflow_type=body.workflow_type,
    )
    knowledge_entries = retrieval_plan.get("persistent_knowledge", [])
    return {
        "brand_name": brand_brief.get("brand_name") or (tenant.name if tenant is not None else ""),
        "brand_tone": brand_brief.get("tone") or str(industry_context.get("recommended_tone", "親切真誠")),
        "brand_address": brand_brief.get("address") or (tenant.address if tenant is not None else ""),
        "industry_context": industry_context,
        "owner_brief": owner_brief,
        "customer_brief": customer_brief,
        "knowledge": [row.get("content", "") for row in knowledge_entries[:6]] or select_knowledge_highlights(repo.list_knowledge_entries(body.tenant_id, limit=6), limit=6),
        "recent_conversations": retrieval_plan.get("recent_conversations", []),
        "active_task_state": retrieval_plan.get("active_task_state", {}),
        "preference_examples": retrieval_plan.get("preference_examples", []),
        "episodes": retrieval_plan.get("episodes", []),
        "retrieval_plan": retrieval_plan,
    }


@router.post("/check-draft-direction")
async def check_draft_direction(body: CheckDraftDirectionRequest, request: Request) -> dict[str, Any]:
    analysis = body.analysis or {}
    context = body.context or {}
    scene = str(analysis.get("scene_description", "本次照片內容") or "本次照片內容")
    tone = str(context.get("brand_tone", "親切真誠") or "親切真誠")
    return {
        "direction_summary": f"延續 {tone} 調性，聚焦 {scene}。",
        "focus_points": [scene],
        "avoidances": ["避免像旁觀者描述照片", "避免過度空泛形容"],
    }


@router.post("/generate-review-reply")
async def generate_review_reply(body: GenerateReviewReplyRequest, request: Request) -> dict[str, Any]:
    review = body.review or {}
    context = body.context or {}
    sentiment = body.sentiment or {}
    consultant = request.app.state.consultant
    brand_name = str(context.get("brand_name", "你的店"))
    reviewer_name = str(review.get("reviewer_name", "顧客"))
    review_content = str(review.get("content", ""))
    strategy = str(sentiment.get("recommended_strategy", "感謝支持並邀請再次光臨"))
    owner_brief = context.get("owner_brief", {}) or {}
    customer_brief = context.get("customer_brief", {}) or {}
    industry_context = context.get("industry_context", {}) or {}
    prompt = (
        f"請幫 {brand_name} 針對顧客 {reviewer_name} 的評論寫一段 80 字內回覆。"
        f"評論：{review_content}。策略：{strategy}。"
        f"品牌語氣：{context.get('brand_tone', '親切真誠')}。"
        f"行業經營重點：{'、'.join(industry_context.get('consultant_focus', [])[:2]) or '維持品牌信任'}。"
        f"老闆近期重點：{'、'.join(owner_brief.get('current_priorities', [])[:2]) or '維持品牌信任'}。"
        f"近期偏好範例：{json.dumps(owner_brief.get('preference_examples', [])[:2], ensure_ascii=False)}。"
        f"顧客狀態摘要：{json.dumps(customer_brief, ensure_ascii=False)}。"
    )
    reply_text = await consultant.build_reply(
        tenant_name=brand_name,
        industry_type="review_reply",
        message=prompt,
    )
    return {"reply_draft": reply_text, "tone": context.get("brand_tone", "親切真誠"), "confidence": 0.8}


@router.post("/generate-content-plan")
async def generate_content_plan(body: GenerateContentPlanRequest, request: Request) -> dict[str, Any]:
    consultant = request.app.state.consultant
    tenant = _repo(request).get_tenant(body.tenant_id)
    plan = await build_content_plan_payload(
        consultant=consultant,
        tenant_name=getattr(tenant, "name", "") or str(body.context.get("brand_name", "你的店") or "你的店"),
        industry_type=getattr(tenant, "industry_type", "") or "一般服務業",
        objective=body.objective,
        selected_platforms=body.selected_platforms,
        context=body.context,
        seed_text=body.seed_text,
        analysis=body.analysis,
    )
    return {"content_plan": plan}


@router.post("/content-plans")
async def create_content_plan(body: CreateContentPlanRequest, request: Request) -> dict[str, Any]:
    from kachu_plus.content_plans import ContentPlanService

    service = ContentPlanService(_repo(request), _settings(request), request.app.state.consultant)
    created = await service.create_plan(
        tenant_id=body.tenant_id,
        objective=body.objective,
        context=body.context,
        selected_platforms=body.selected_platforms,
        scheduled_for=body.scheduled_for,
        source_conversation_id=body.source_conversation_id,
        created_by_line_user_id=body.created_by_line_user_id,
    )
    plan = created["content_plan"]
    item = created["item"]
    return {
        "content_plan": {
            "id": plan.id,
            "tenant_id": plan.tenant_id,
            "objective": plan.objective,
            "status": plan.status,
        },
        "item": {
            "id": item.id,
            "status": item.status,
            "scheduled_for": item.scheduled_for.isoformat() if item.scheduled_for else None,
        },
        "content_plan_payload": created["content_plan_payload"],
    }


@router.post("/generate-drafts")
async def generate_drafts(body: GenerateDraftsRequest, request: Request) -> dict[str, Any]:
    consultant = request.app.state.consultant
    content_plan = body.content_plan or body.workflow_input.get("content_plan") or {}
    return await build_content_drafts(
        consultant=consultant,
        context=body.context or {},
        analysis=body.analysis or {},
        content_plan=content_plan,
    )


@router.post("/notify-approval")
async def notify_approval(body: NotifyApprovalRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    pending = repo.save_pending_approval(
        tenant_id=body.tenant_id,
        agentos_task_id=body.task_id,
        agentos_run_id=body.run_id,
        workflow_type=body.workflow,
        draft_content=json.dumps(body.drafts, ensure_ascii=False),
        review_id=body.review_id,
    )
    settings = _settings(request)
    recipients = resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id=body.tenant_id)
    line_access_token = resolve_line_push_access_token(repo=repo, settings=settings, tenant_id=body.tenant_id)
    push_errors: list[str] = []
    delivered_count = 0
    if not recipients:
        push_errors.append("no active LINE recipients")
    if recipients and not line_access_token:
        push_errors.append("missing LINE access token")
    if recipients and line_access_token:
        review_payload = body.drafts.get("reply_draft", "")
        review_text = review_payload.get("reply_draft", "") if isinstance(review_payload, dict) else str(review_payload)
        google_text = str(body.drafts.get("post_text") or body.drafts.get("google") or "")
        flex_content = None
        alt_text = "新任務草稿準備好了，請確認"
        if body.workflow == "kachu_review_reply":
            flex_content = build_review_reply_flex(
                run_id=body.run_id,
                tenant_id=body.tenant_id,
                review_content=str(body.drafts.get("review_content", "")),
                reply_draft=review_text,
            )
            alt_text = "新評論回覆草稿"
        elif body.workflow == "kachu_google_post":
            flex_content = build_google_post_flex(
                run_id=body.run_id,
                tenant_id=body.tenant_id,
                post_text=google_text,
            )
            alt_text = "Google 商家動態草稿"
        elif body.workflow == "kachu_knowledge_update":
            flex_content = build_knowledge_update_flex(
                run_id=body.run_id,
                tenant_id=body.tenant_id,
                drafts=body.drafts,
            )
            alt_text = "知識庫更新確認"
        elif body.workflow == "kachu_photo_content":
            flex_content = build_photo_content_flex(
                run_id=body.run_id,
                tenant_id=body.tenant_id,
                drafts=body.drafts,
            )
            alt_text = "新貼文草稿準備好了"
        elif body.workflow == "kachu_planned_content":
            flex_content = build_planned_content_flex(
                run_id=body.run_id,
                tenant_id=body.tenant_id,
                drafts=body.drafts,
            )
            alt_text = "企劃排程草稿"
        elif body.workflow == "kachu_meta_reply":
            flex_content = build_external_reply_flex(
                run_id=body.run_id,
                tenant_id=body.tenant_id,
                source_label=str(body.drafts.get("source_label") or "Meta 留言"),
                customer_name=str(body.drafts.get("author_name") or "顧客"),
                incoming_text=str(body.drafts.get("incoming_text") or ""),
                reply_draft=review_text,
            )
            alt_text = "外部互動回覆草稿"
        for recipient in recipients:
            messages = [
                {"type": "flex", "altText": alt_text, "contents": flex_content}
            ] if flex_content is not None else [text_message(f"有一項待確認草稿\n請到系統確認。 run_id={body.run_id}")]
            try:
                await push_line_messages(
                    to=recipient,
                    messages=messages,
                    access_token=line_access_token,
                )
                delivered_count += 1
            except Exception as exc:
                import logging as _logging
                _logging.getLogger(__name__).warning(
                    "notify_approval: LINE push failed for recipient=%s run_id=%s: %s",
                    recipient, body.run_id, exc,
                )
                push_errors.append(str(exc))
    if push_errors and delivered_count == 0:
        repo.update_pending_approval_status(
            agentos_run_id=body.run_id,
            status="delivery_failed",
            actor_line_id="system",
            decision_payload={
                "stage": "notify_approval",
                "push_warnings": push_errors,
                "recipient_count": len(recipients),
            },
        )
    if push_errors:
        return {"status": "notified", "approval_record_id": pending.id, "push_warnings": push_errors}
    return {"status": "notified", "approval_record_id": pending.id}


@router.post("/parse-knowledge-update")
async def parse_knowledge_update(body: ParseKnowledgeUpdateRequest, request: Request) -> dict[str, Any]:
    message = body.boss_message.strip()
    subject, old_value, new_value = _extract_update_values(message)
    parsed_update = {
        "update_type": _infer_update_type(message),
        "category": _infer_knowledge_category(message),
        "subject": subject,
        "old_value": old_value,
        "new_value": new_value,
        "keywords": _build_knowledge_keywords(message, subject, new_value),
    }
    return {
        "run_id": body.run_id,
        "boss_message": body.boss_message,
        "parsed_update": parsed_update,
    }


@router.post("/diff-knowledge")
async def diff_knowledge(body: DiffKnowledgeRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    parsed = body.parsed_update or {}
    category = str(parsed.get("category") or "")
    keywords = [str(item).strip() for item in parsed.get("keywords", []) if str(item).strip()]
    matched_entries = repo.list_knowledge_entries(body.tenant_id, limit=50)
    conflicting_entries = []
    for entry in matched_entries:
        if category and entry.category != category:
            continue
        haystack = entry.content.lower()
        if keywords and not any(keyword.lower() in haystack for keyword in keywords):
            continue
        conflicting_entries.append(
            {
                "entry_id": entry.id,
                "category": entry.category,
                "content": entry.content[:200],
            }
        )
        if len(conflicting_entries) >= 5:
            break
    diff_summary = (
        f"找到 {len(conflicting_entries)} 條可能需要更新的知識條目。"
        if conflicting_entries
        else "知識庫中沒有找到相關的既有條目，將新增一條。"
    )
    return {
        "run_id": body.run_id,
        "parsed_update": parsed,
        "conflicting_entries": conflicting_entries,
        "diff_summary": diff_summary,
    }


@router.post("/apply-knowledge-update")
async def apply_knowledge_update(body: ApplyKnowledgeUpdateRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    diff = body.diff or {}
    parsed = diff.get("parsed_update", {}) or {}
    new_content = str(parsed.get("new_value") or "").strip()
    if not new_content:
        raise HTTPException(status_code=400, detail="new_value is required")
    category = str(parsed.get("category") or "product")
    repo.save_knowledge_entry(
        tenant_id=body.tenant_id,
        category=category,
        content=new_content,
        source_type="boss_update",
    )
    return {
        "status": "applied",
        "run_id": body.run_id,
        "category": category,
        "new_content": new_content,
        "conflict_count": len(diff.get("conflicting_entries", [])),
    }


@router.post("/send-or-escalate")
async def send_or_escalate(body: SendOrEscalateRequest, request: Request) -> dict[str, Any]:
    return await send_or_escalate_customer_response(
        repo=_repo(request),
        settings=_settings(request),
        tenant_id=body.tenant_id,
        customer_line_id=body.customer_line_id,
        message=body.message,
        answer=body.answer,
        recipient_line_ids=body.recipient_line_ids,
    )


@router.post("/determine-post-type")
def determine_post_type(body: DeterminePostTypeRequest) -> dict[str, Any]:
    topic = body.topic.strip()
    if any(keyword in topic for keyword in ("優惠", "折扣", "買一送一")):
        return {"post_type": "OFFER"}
    if any(keyword in topic for keyword in ("活動", "開幕", "講座", "節日")):
        return {"post_type": "EVENT"}
    return {"post_type": "STANDARD"}


@router.post("/generate-google-post")
async def generate_google_post(body: GenerateGooglePostRequest, request: Request) -> dict[str, Any]:
    context = body.context or {}
    consultant = request.app.state.consultant
    brand_name = str(context.get("brand_name", "你的店"))
    address = str(context.get("brand_address", ""))
    content_plan = context.get("content_plan", {}) or {}
    owner_brief = context.get("owner_brief", {}) or {}
    customer_brief = context.get("customer_brief", {}) or {}
    industry_context = context.get("industry_context", {}) or {}
    campaign_angle = str(content_plan.get("campaign_angle") or body.topic or "本週新消息")
    call_to_action = str(content_plan.get("call_to_action") or "歡迎私訊了解更多。")
    prompt = (
        f"請為 {brand_name} 撰寫一則 Google 商家動態，主題是 {body.topic or '本週新消息'}，"
        f"型態為 {body.post_type}，120 字內，繁體中文，保留行動呼籲。地址：{address}。"
        f"企劃角度：{campaign_angle}。CTA：{call_to_action}。"
        f"品牌語氣：{context.get('brand_tone', '親切真誠')}。"
        f"建議內容角度：{'、'.join(industry_context.get('content_angles', [])[:2]) or '品牌亮點'}。"
        f"本月市場重點：{'、'.join(item.get('theme', '') for item in industry_context.get('market_calendar', [])[:1]) or '一般經營'}。"
        f"老闆近期重點：{'、'.join(owner_brief.get('current_priorities', [])[:2]) or '維持穩定曝光'}。"
        f"老闆偏好範例：{json.dumps(owner_brief.get('preference_examples', [])[:2], ensure_ascii=False)}。"
        f"顧客摘要：{json.dumps(customer_brief, ensure_ascii=False)}。"
        f"品牌知識：{'、'.join(context.get('knowledge', [])[:3]) or '無'}。"
    )
    reply_text = await consultant.build_reply(
        tenant_name=brand_name,
        industry_type="google_post",
        message=prompt,
    )
    return {
        "post_text": reply_text,
        "post_type": body.post_type,
        "topic": body.topic,
        "selected_platforms": ["google"],
        "google": reply_text,
    }


@router.post("/publish-google-post")
def publish_google_post(body: PublishGooglePostRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    service = _review_service(request)
    post_text = body.post_text or (body.drafts or {}).get("post_text") or (body.drafts or {}).get("google", "")
    if not post_text:
        return {"status": "skipped", "reason": "empty post text"}
    try:
        client, account_id, location_id = service.resolve_client_context(body.tenant_id)
        result = client.create_local_post(account_id=account_id, location_id=location_id, summary=post_text, call_to_action_url=body.call_to_action_url)
        repo.record_published_content(
            tenant_id=body.tenant_id,
            workflow_type="kachu_google_post",
            channel="google_business",
            source_id=body.run_id,
            source_ref=str(result.get("name", "")),
            content_text=post_text,
            payload=result,
        )
        repo.decide_pending_approval(
            agentos_run_id=body.run_id,
            decision="published",
            actor_line_id="system",
            decision_payload={"post_text": post_text, "result": result},
        )
        return {"status": "published", "post_name": result.get("name", "")}
    except GoogleBusinessConnectorError as exc:
        repo.decide_pending_approval(
            agentos_run_id=body.run_id,
            decision="delivery_failed",
            actor_line_id="system",
            decision_payload={"post_text": post_text, "error": str(exc)},
        )
        return {"status": "failed", "error": str(exc)}


@router.post("/publish-content")
def publish_content(body: PublishContentRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    drafts = body.drafts or {}
    results = publish_content_bundle(
        repo=repo,
        review_service=_review_service(request),
        tenant_id=body.tenant_id,
        run_id=body.run_id,
        drafts=drafts,
        selected_platforms=body.selected_platforms,
        workflow_type="kachu_photo_content",
    )
    if results:
        repo.decide_pending_approval(
            agentos_run_id=body.run_id,
            decision="published" if publish_content_bundle_succeeded(results) else "delivery_failed",
            actor_line_id="system",
            decision_payload={"results": results},
        )
    return results


@router.post("/post-review-reply")
def post_review_reply(body: PostReviewReplyRequest, request: Request) -> dict[str, Any]:
    repo = _repo(request)
    reply_payload = body.reply or {}
    reply_text = str(reply_payload.get("reply_draft") or reply_payload.get("text") or body.confirmation.get("reply_draft") or "")
    result = publish_review_reply(
        repo=repo,
        review_service=_review_service(request),
        tenant_id=body.tenant_id,
        run_id=body.run_id,
        review_id=body.review_id,
        reply_text=reply_text,
    )
    repo.decide_pending_approval(
        agentos_run_id=body.run_id,
        decision="published" if result.get("status") == "posted" else "delivery_failed",
        actor_line_id="system",
        decision_payload={"reply_draft": reply_text, "result": result},
    )
    return {"status": result.get("status", "skipped"), "review_id": body.review_id}


@router.get("/tenants/{tenant_id}/approvals/{run_id}")
def get_pending_approval(tenant_id: str, run_id: str, request: Request) -> dict[str, Any]:
    pending = _repo(request).get_pending_approval_by_run_id(run_id)
    if pending is None or pending.tenant_id != tenant_id:
        raise HTTPException(status_code=404, detail="pending approval not found")
    drafts = json.loads(pending.draft_content or "{}")
    return {
        "run_id": run_id,
        "workflow_type": pending.workflow_type,
        "status": pending.status,
        "drafts": drafts,
    }