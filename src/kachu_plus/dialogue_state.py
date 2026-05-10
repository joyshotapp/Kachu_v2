from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from kachu_plus.models import BossRouteDecision, BossRouteMode

_STATUS_SIGNALS = ("草稿", "進度", "好了嗎", "好了沒", "完成了嗎", "完成沒", "狀態", "在哪", "怎麼還沒")
_REFERENCE_SIGNALS = ("那個", "那篇", "上一個", "剛剛", "前面", "上次", "這個", "它")
_REVISION_SIGNALS = ("改", "修改", "重寫", "重來", "補上", "加上", "改成", "換成", "短一點", "長一點", "不要", "太")
_CONSULT_CARRY_SIGNALS = ("那你覺得", "那現在", "那我要", "那應該", "接下來呢", "所以呢")


def _contains_any(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


@dataclass
class DialogueState:
    is_follow_up: bool = False
    inferred_mode: BossRouteMode | None = None
    inferred_intent_label: str = ""
    carry_over_task_id: str = ""
    carry_over_run_id: str = ""
    carry_over_source_text: str = ""
    reason: str = ""
    recent_conversation_ids: list[str] = field(default_factory=list)

    def apply(self, decision: BossRouteDecision) -> BossRouteDecision:
        if self.inferred_mode is None:
            return decision
        if decision.mode != BossRouteMode.CLARIFY:
            if self.inferred_mode == BossRouteMode.EXECUTE and decision.mode == BossRouteMode.EXECUTE:
                if not decision.intent_label and self.inferred_intent_label:
                    decision.intent_label = self.inferred_intent_label
            return decision
        if self.inferred_mode == BossRouteMode.EXECUTE:
            return BossRouteDecision(mode=BossRouteMode.EXECUTE, intent_label=self.inferred_intent_label)
        if self.inferred_mode == BossRouteMode.CONSULT:
            return BossRouteDecision(
                mode=BossRouteMode.CONSULT,
                consult_reply="我知道你是在承接上一輪脈絡，我會接著前面的狀態一起看。",
            )
        return decision


class DialogueStateResolver:
    def __init__(self, repo: Any) -> None:
        self._repo = repo

    def resolve(
        self,
        *,
        tenant_id: str,
        line_user_id: str,
        text: str,
    ) -> DialogueState:
        normalized = str(text or "").strip()
        latest_task = self._repo.get_latest_execute_task_record(tenant_id=tenant_id, line_user_id=line_user_id)
        recent = self._repo.list_recent_conversations(tenant_id, line_user_id=line_user_id, limit=6)
        recent_ids = [str(getattr(row, "id", "") or "") for row in recent]

        state = DialogueState(recent_conversation_ids=recent_ids)
        if not normalized:
            return state

        if latest_task is not None and _contains_any(normalized, _STATUS_SIGNALS):
            state.is_follow_up = True
            state.inferred_mode = BossRouteMode.EXECUTE
            state.inferred_intent_label = "draft_status"
            state.carry_over_task_id = str(getattr(latest_task, "task_id", "") or "")
            state.carry_over_run_id = str(getattr(latest_task, "run_id", "") or "")
            state.carry_over_source_text = str(getattr(latest_task, "source_text", "") or "")
            state.reason = "active_task_status_follow_up"
            return state

        if latest_task is not None and (
            _contains_any(normalized, _REVISION_SIGNALS)
            and (_contains_any(normalized, _REFERENCE_SIGNALS) or len(normalized) <= 18)
        ):
            state.is_follow_up = True
            state.inferred_mode = BossRouteMode.EXECUTE
            state.inferred_intent_label = str(getattr(latest_task, "intent_label", "") or "")
            state.carry_over_task_id = str(getattr(latest_task, "task_id", "") or "")
            state.carry_over_run_id = str(getattr(latest_task, "run_id", "") or "")
            state.carry_over_source_text = str(getattr(latest_task, "source_text", "") or "")
            state.reason = "active_task_revision_follow_up"
            return state

        if recent and (
            _contains_any(normalized, _CONSULT_CARRY_SIGNALS)
            or (len(normalized) <= 12 and _contains_any(normalized, _REFERENCE_SIGNALS))
        ):
            state.is_follow_up = True
            state.inferred_mode = BossRouteMode.CONSULT
            state.reason = "recent_dialogue_consult_carry_over"
            return state

        return state