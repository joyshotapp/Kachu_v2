from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from sqlmodel import SQLModel, Session, create_engine

from kachu_plus.config import Settings
from kachu_plus.dialogue_state import DialogueStateResolver
from kachu_plus.evaluation import (
    compute_consult_groundedness,
    compute_follow_up_routing_accuracy,
    compute_memory_promotion_scores,
    compute_preference_reuse_rate,
    compute_retrieval_hit_rate,
    compute_task_follow_up_success_rate,
)
from kachu_plus.learning import ConversationLearningService, MemoryManager
from kachu_plus.memory_promotion import ConversationMemoryPromoter
from kachu_plus.models import BossRouteMode
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import TenantTable
from kachu_plus.retrieval_plan import RetrievalPlanComposer


def test_retrieval_plan_composer_ranks_matching_knowledge_and_active_task() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="測試店", industry_type="咖啡廳"))
        session.commit()

    repo.save_knowledge_entry(tenant_id="tenant-1", category="basic_info", content="營業時間是每天 10:00 到 20:00")
    repo.save_knowledge_entry(tenant_id="tenant-1", category="product", content="我們主打手沖咖啡與社區熟客經營")
    repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U1",
        actor_role="boss",
        channel_type="line",
        conversation_kind="boss_consult",
        content_text="最近想提升熟客回購",
    )
    repo.save_execute_task_record(
        tenant_id="tenant-1",
        line_user_id="U1",
        intent_label="google_post",
        source_text="幫我寫一篇熟客回購貼文",
        objective="Create a Google post draft",
        task_id="task-1",
        run_id="run-1",
        status="running",
    )

    composer = RetrievalPlanComposer(repo, MemoryManager(repo, Settings()))
    plan = composer.compose(tenant_id="tenant-1", query="熟客回購怎麼提升", line_user_id="U1", workflow_type="consult")

    assert plan["active_task_state"]["task_id"] == "task-1"
    assert plan["persistent_knowledge"][0]["category"] == "product"
    assert any(item["conversation_kind"] == "boss_consult" for item in plan["recent_conversations"])


def test_memory_promoter_promotes_fact_preference_and_episode() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="測試店"))
        session.commit()

    promoter = ConversationMemoryPromoter(repo)
    fact = repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U1",
        actor_role="boss",
        channel_type="line",
        conversation_kind="boss_command",
        content_text="我們的營業時間改成每天 10 點到 8 點",
    )
    preference = repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U1",
        actor_role="boss",
        channel_type="line",
        conversation_kind="follow_up",
        content_text="語氣不要太像廣告，想要更口語一點",
    )
    episode = repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U1",
        actor_role="boss",
        channel_type="line",
        conversation_kind="follow_up",
        content_text="可以，就發這篇",
    )

    fact_result = promoter.promote_conversation(tenant_id="tenant-1", conversation=fact)
    pref_result = promoter.promote_conversation(tenant_id="tenant-1", conversation=preference)
    episode_result = promoter.promote_conversation(
        tenant_id="tenant-1",
        conversation=episode,
        latest_task=SimpleNamespace(task_id="task-1", run_id="run-1", intent_label="google_post"),
    )

    knowledge = repo.list_knowledge_entries("tenant-1", limit=10)
    preferences = repo.get_preference_memories("tenant-1", platform="google", limit=10)
    episodes = repo.get_episodic_memories("tenant-1", workflow_type="google_post", limit=10)

    assert fact_result["knowledge"] == 1
    assert pref_result["preferences"] == 1
    assert episode_result["episodes"] == 1
    assert any(item.category == "basic_info" for item in knowledge)
    assert any("更口語" in item.edited_draft for item in preferences)
    assert any(item.outcome == "accepted" for item in episodes)


def test_conversation_learning_service_promotes_safety_concern_into_knowledge() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="測試店"))
        session.commit()

    conversation = repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U1",
        actor_role="boss",
        channel_type="line",
        conversation_kind="boss_consult",
        content_text="最近很多客人都很在意安全性，也會追問有沒有副作用。",
    )

    result = ConversationLearningService(repo).absorb_conversation(
        tenant_id="tenant-1",
        line_user_id="U1",
        conversation=conversation,
    )

    knowledge = repo.list_knowledge_entries("tenant-1", limit=10)
    assert result["knowledge"] == 1
    assert any(item.category == "pain_point" and "安全性" in item.content for item in knowledge)


def test_dialogue_state_resolver_carries_status_and_consult_followups() -> None:
    repo = MagicMock()
    repo.get_latest_execute_task_record.return_value = SimpleNamespace(
        task_id="task-1",
        run_id="run-1",
        intent_label="google_post",
        source_text="幫我寫母親節貼文",
    )
    repo.list_recent_conversations.return_value = [
        SimpleNamespace(id="c1", conversation_kind="boss_consult", content_text="先討論回購策略")
    ]
    resolver = DialogueStateResolver(repo)

    status_state = resolver.resolve(tenant_id="tenant-1", line_user_id="U1", text="那篇草稿好了嗎")
    consult_state = resolver.resolve(tenant_id="tenant-1", line_user_id="U1", text="那接下來呢")

    assert status_state.inferred_mode == BossRouteMode.EXECUTE
    assert status_state.inferred_intent_label == "draft_status"
    assert consult_state.inferred_mode == BossRouteMode.CONSULT


def test_evaluation_metrics_cover_phase5_kpis() -> None:
    routing = compute_follow_up_routing_accuracy([
        {"matched": True},
        {"matched": False},
        {"matched": True},
    ])
    retrieval = compute_retrieval_hit_rate([
        {"expected_ids": ["k1"], "retrieved_ids": ["k1", "k2"]},
        {"expected_ids": ["k9"], "retrieved_ids": ["k3"]},
    ])
    promotion = compute_memory_promotion_scores([
        {"predicted": ["knowledge", "preference"], "expected": ["knowledge"]},
        {"predicted": ["episode"], "expected": ["episode"]},
    ])
    grounded = compute_consult_groundedness(
        "你可以先放大社區熟客經營，並延續每日 10:00 到 20:00 的營業節奏。",
        {
            "relevant_knowledge": ["主打社區熟客經營", "營業時間是每天 10:00 到 20:00"],
            "recent_conversations": [{"content_text": "最近想提升熟客回購"}],
        },
    )
    reuse = compute_preference_reuse_rate([
        {"generated_text": "整體語氣更口語，也保留 CTA", "preference_phrases": ["更口語", "CTA"]},
        {"generated_text": "正式公告版本", "preference_phrases": ["更口語"]},
    ])
    follow_up = compute_task_follow_up_success_rate([
        {"resolved": True},
        {"resolved": True},
        {"resolved": False},
    ])

    assert routing["accuracy"] == 0.6667
    assert retrieval["hit_rate"] == 0.5
    assert promotion["precision"] == 0.6667
    assert promotion["recall"] == 1.0
    assert grounded["groundedness"] > 0
    assert reuse["reuse_rate"] == 0.5
    assert follow_up["success_rate"] == 0.6667