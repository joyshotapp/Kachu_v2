from __future__ import annotations

from typing import Any

from kachu_plus.dialogue_state import DialogueState
from kachu_plus.models import BossRouteDecision, BossRouteMode, ConversationResponsePlan

_TRAFFIC_SIGNALS = ("流量", "成效", "觸及", "互動", "曝光", "預約變少", "數據")
_REVIEW_SIGNALS = ("評論", "評價", "留言", "負評", "差評", "一星")
_CUSTOMER_SIGNALS = ("客人", "顧客", "回購", "回來", "沉睡", "流失", "沒來")
_CONTENT_SIGNALS = ("貼文", "發文", "文案", "商家動態", "內容", "企劃")
_PROFILE_SIGNALS = ("營業", "地址", "電話", "菜單", "公休", "店休", "Google")
_HELP_SIGNALS = ("幫忙", "有問題", "需要幫忙", "想做點什麼", "有件事")
_EMOTION_URGENT = ("急", "趕快", "快點", "救命", "來不及")
_EMOTION_STRESS = ("焦慮", "擔心", "煩", "頭痛", "崩潰", "慘", "糟", "差", "不太好", "不好")
_EMOTION_FRUSTRATION = ("生氣", "火大", "氣死", "受不了", "沒看到", "怎麼都")


def _contains_any(text: str, signals: tuple[str, ...]) -> bool:
    normalized = str(text or "")
    return any(token in normalized for token in signals)


def _clean_text(text: str) -> str:
    return str(text or "").strip()


def _summarize_recent_conversations(recent_conversations: list[Any]) -> str:
    snippets: list[str] = []
    for row in recent_conversations[:3]:
        content = _clean_text(getattr(row, "content_text", ""))
        role = _clean_text(getattr(row, "actor_role", "")) or "unknown"
        if content:
            snippets.append(f"{role}:{content[:24]}")
    return " | ".join(snippets)


def _detect_emotional_signals(text: str) -> list[str]:
    signals: list[str] = []
    if _contains_any(text, _EMOTION_URGENT):
        signals.append("urgent")
    if _contains_any(text, _EMOTION_STRESS):
        signals.append("stress")
    if _contains_any(text, _EMOTION_FRUSTRATION):
        signals.append("frustration")
    return signals


def _build_empathy_prefix(emotional_signals: list[str]) -> str:
    if "frustration" in emotional_signals:
        return "我知道你現在對這件事有點不爽，我先陪你把重點抓清楚。"
    if "urgent" in emotional_signals:
        return "我知道你現在很急，我先幫你把問題拆成最短下一步。"
    if "stress" in emotional_signals:
        return "我知道你現在有點擔心，我先陪你把問題釐清。"
    return ""


def _infer_user_goal(text: str, decision: BossRouteDecision) -> str:
    if decision.mode == BossRouteMode.EXECUTE:
        return f"直接執行 {decision.intent_label or '商務動作'}"
    if _contains_any(text, _TRAFFIC_SIGNALS):
        return "想理解流量或成效問題"
    if _contains_any(text, _REVIEW_SIGNALS):
        return "想處理評論或評價"
    if _contains_any(text, _CUSTOMER_SIGNALS):
        return "想改善客人回流或找出流失原因"
    if _contains_any(text, _CONTENT_SIGNALS):
        return "想處理貼文、內容或發文方向"
    if _contains_any(text, _PROFILE_SIGNALS):
        return "想處理店家資訊或營運資訊"
    return "想先把眼前的商務問題講清楚"


def _build_context_summary(
    *,
    tenant: Any,
    latest_task: Any,
    recent_conversations: list[Any],
    dialogue_state: DialogueState,
) -> str:
    segments: list[str] = []
    tenant_name = _clean_text(getattr(tenant, "name", ""))
    industry_type = _clean_text(getattr(tenant, "industry_type", ""))
    if tenant_name:
        segments.append(f"brand={tenant_name}")
    if industry_type:
        segments.append(f"industry={industry_type}")
    if latest_task is not None:
        intent_label = _clean_text(getattr(latest_task, "intent_label", ""))
        status = _clean_text(getattr(latest_task, "status", ""))
        if intent_label or status:
            segments.append(f"active_task={intent_label or 'unknown'}:{status or 'unknown'}")
    recent_summary = _summarize_recent_conversations(recent_conversations)
    if recent_summary:
        segments.append(f"recent={recent_summary}")
    if dialogue_state.is_follow_up:
        segments.append(f"follow_up={dialogue_state.reason or 'true'}")
    return "；".join(segments)


def _build_clarify_question(text: str, dialogue_state: DialogueState, emotional_signals: list[str]) -> str:
    empathy = _build_empathy_prefix(emotional_signals)
    if dialogue_state.is_follow_up and dialogue_state.carry_over_task_id:
        question = "你這句是要我接著上一個任務往下改，還是這次想換成新的需求？"
    elif _contains_any(text, _TRAFFIC_SIGNALS):
        question = "你是要我先拉報告看數字，還是先跟你拆可能原因？"
    elif _contains_any(text, _REVIEW_SIGNALS):
        question = "你是要我直接幫你回這則評論，還是先一起判斷怎麼處理比較好？"
    elif _contains_any(text, _CUSTOMER_SIGNALS):
        question = "你是想先找出哪些客人變少或沒回來，還是先討論為什麼回購掉了？"
    elif _contains_any(text, _CONTENT_SIGNALS):
        question = "你是要我直接幫你寫貼文，還是先一起定這次主題與方向？"
    elif _contains_any(text, _PROFILE_SIGNALS):
        question = "你是要我直接幫你更新店家資訊，還是先列出這次要改哪些欄位？"
    elif _contains_any(text, _HELP_SIGNALS):
        question = "你現在最想先處理哪一件：貼文、評論、流量，還是客人回購？"
    else:
        question = "你現在最想先處理哪一件：貼文、評論、流量，還是客人回購？"
    return f"{empathy}\n{question}" if empathy else question


def _build_consult_directive(
    *,
    text: str,
    user_goal: str,
    context_summary: str,
    dialogue_state: DialogueState,
    emotional_signals: list[str],
) -> str:
    directive_parts = [f"請直接回答老闆這句話真正想問的事：{_clean_text(text)}。"]
    if user_goal:
        directive_parts.append(f"推定老闆目標：{user_goal}。")
    if emotional_signals:
        directive_parts.append("先用一句話接住情緒，但不要過度安撫，也不要空話。")
    if dialogue_state.is_follow_up:
        directive_parts.append("這是延續上一輪的 follow-up，優先承接前文，不要當成新主題。")
    if context_summary:
        directive_parts.append(f"優先利用這些脈絡：{context_summary}。")
    directive_parts.append("避免 generic 澄清，若資訊足夠就直接給判斷。")
    directive_parts.append("回答格式：先給結論，再補 2 個具體理由，最後給一個最短可執行下一步。")
    return " ".join(directive_parts)


class ConversationResponsePlanner:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def plan(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        text: str,
        decision: BossRouteDecision,
        dialogue_state: DialogueState,
    ) -> ConversationResponsePlan:
        tenant = self._repo.get_tenant(tenant_id)
        latest_task = None
        if line_user_id:
            latest_task = self._repo.get_latest_execute_task_record(tenant_id=tenant_id, line_user_id=line_user_id)
        if latest_task is None:
            latest_task = self._repo.get_latest_execute_task_for_tenant(tenant_id)
        recent_conversations = self._repo.list_recent_conversations(
            tenant_id,
            line_user_id=line_user_id or None,
            limit=4,
        )
        emotional_signals = _detect_emotional_signals(text)
        reasoning_signals: list[str] = []
        user_goal = _infer_user_goal(text, decision)
        context_summary = _build_context_summary(
            tenant=tenant,
            latest_task=latest_task,
            recent_conversations=recent_conversations,
            dialogue_state=dialogue_state,
        )

        if decision.mode == BossRouteMode.EXECUTE:
            reasoning_signals.append(f"execute:{decision.intent_label or 'general'}")
            return ConversationResponsePlan(
                mode=decision.mode,
                intent_label=decision.intent_label,
                response_strategy="execute",
                user_goal=user_goal,
                context_summary=context_summary,
                confidence=0.98,
                reasoning_signals=reasoning_signals,
                emotional_signals=emotional_signals,
            )

        if decision.mode == BossRouteMode.CONSULT:
            if decision.intent_label in {"greeting", "capability_overview"} and decision.consult_reply:
                reasoning_signals.append(f"fast_path:{decision.intent_label}")
                return ConversationResponsePlan(
                    mode=decision.mode,
                    intent_label=decision.intent_label,
                    response_strategy=decision.intent_label,
                    user_goal=user_goal,
                    context_summary=context_summary,
                    consult_reply=decision.consult_reply,
                    confidence=0.99,
                    reasoning_signals=reasoning_signals,
                    emotional_signals=emotional_signals,
                )

            reasoning_signals.append("consult:llm")
            return ConversationResponsePlan(
                mode=decision.mode,
                intent_label=decision.intent_label,
                response_strategy="consult_llm",
                user_goal=user_goal,
                context_summary=context_summary,
                reply_directive=_build_consult_directive(
                    text=text,
                    user_goal=user_goal,
                    context_summary=context_summary,
                    dialogue_state=dialogue_state,
                    emotional_signals=emotional_signals,
                ),
                confidence=0.84,
                reasoning_signals=reasoning_signals,
                emotional_signals=emotional_signals,
            )

        reasoning_signals.append("clarify:targeted")
        return ConversationResponsePlan(
            mode=decision.mode,
            intent_label=decision.intent_label,
            response_strategy="empathy_clarify" if emotional_signals else "ask_targeted_question",
            user_goal=user_goal,
            context_summary=context_summary,
            clarify_question=_build_clarify_question(text, dialogue_state, emotional_signals),
            confidence=0.72,
            reasoning_signals=reasoning_signals,
            emotional_signals=emotional_signals,
        )