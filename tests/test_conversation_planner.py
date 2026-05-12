from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from kachu_plus.conversation_planner import ConversationResponsePlanner
from kachu_plus.dialogue_state import DialogueState
from kachu_plus.models import BossRouteDecision, BossRouteMode


def _make_repo() -> MagicMock:
    repo = MagicMock()
    repo.get_tenant.return_value = SimpleNamespace(name="測試店", industry_type="餐廳")
    repo.get_latest_execute_task_record.return_value = None
    repo.get_latest_execute_task_for_tenant.return_value = None
    repo.list_recent_conversations.return_value = []
    return repo


def test_greeting_plan_uses_fast_path() -> None:
    repo = _make_repo()
    planner = ConversationResponsePlanner(repo)

    plan = planner.plan(
        tenant_id="tenant-1",
        line_user_id="U1",
        text="你好",
        decision=BossRouteDecision(mode=BossRouteMode.CONSULT, intent_label="greeting", consult_reply="你好，我在。"),
        dialogue_state=DialogueState(),
    )

    assert plan.response_strategy == "greeting"
    assert plan.consult_reply.startswith("你好，我在")


def test_traffic_clarify_is_targeted() -> None:
    repo = _make_repo()
    planner = ConversationResponsePlanner(repo)

    plan = planner.plan(
        tenant_id="tenant-1",
        line_user_id="U1",
        text="最近流量掉很多",
        decision=BossRouteDecision(mode=BossRouteMode.CLARIFY),
        dialogue_state=DialogueState(),
    )

    assert plan.response_strategy == "ask_targeted_question"
    assert "拉報告看數字" in plan.clarify_question
    assert "拆可能原因" in plan.clarify_question


def test_review_clarify_is_targeted() -> None:
    repo = _make_repo()
    planner = ConversationResponsePlanner(repo)

    plan = planner.plan(
        tenant_id="tenant-1",
        line_user_id="U1",
        text="有個評論",
        decision=BossRouteDecision(mode=BossRouteMode.CLARIFY),
        dialogue_state=DialogueState(),
    )

    assert "直接幫你回這則評論" in plan.clarify_question


def test_emotional_clarify_adds_empathy() -> None:
    repo = _make_repo()
    planner = ConversationResponsePlanner(repo)

    plan = planner.plan(
        tenant_id="tenant-1",
        line_user_id="U1",
        text="最近生意很差，我有點焦慮",
        decision=BossRouteDecision(mode=BossRouteMode.CLARIFY),
        dialogue_state=DialogueState(),
    )

    assert plan.response_strategy == "empathy_clarify"
    assert "我知道你現在有點擔心" in plan.clarify_question


def test_consult_plan_builds_reply_directive() -> None:
    repo = _make_repo()
    repo.list_recent_conversations.return_value = [
        SimpleNamespace(actor_role="boss", content_text="上週貼文成效不錯"),
        SimpleNamespace(actor_role="ai", content_text="建議延續高互動題材"),
    ]
    planner = ConversationResponsePlanner(repo)

    plan = planner.plan(
        tenant_id="tenant-1",
        line_user_id="U1",
        text="你覺得我接下來該先衝來客還是先衝回購？",
        decision=BossRouteDecision(mode=BossRouteMode.CONSULT),
        dialogue_state=DialogueState(),
    )

    assert plan.response_strategy == "consult_llm"
    assert "直接回答老闆這句話真正想問的事" in plan.reply_directive
    assert "回答格式：先給結論" in plan.reply_directive