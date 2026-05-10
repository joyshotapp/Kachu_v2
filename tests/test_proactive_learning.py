from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, Session, create_engine, select

from kachu_plus.config import Settings
from kachu_plus.learning import ContextBriefManager, MemoryManager, PostTaskReviewService
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import CustomerProfileTable, KnowledgeEntryTable, SuggestionTable, TenantTable
from kachu_plus.proactive import (
    KachuExecutionPolicyResolver,
    META_INSIGHTS_REPORT_JOB,
    NUDGE_NEGATIVE_REVIEW,
    NUDGE_NO_POST,
    NUDGE_SLEEPING_CUSTOMERS,
    NUDGE_STALE_KNOWLEDGE,
    PROACTIVE_SCAN_JOB,
    ProactiveSuggestionEngine,
    ProactiveSuggestionScheduler,
)


def _make_engine():
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )


def _seed_tenant(repo: KachuPlusRepository, tenant_id: str = "tenant-1") -> None:
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(TenantTable(id=tenant_id, name="測試店", industry_type="cafe", address="台北市信義區", sleep_threshold=30))
        session.add(KnowledgeEntryTable(tenant_id=tenant_id, category="core_value", content="手作甜點與親切服務"))
        session.commit()


def test_proactive_engine_detects_all_three_nudges() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    proactive = ProactiveSuggestionEngine(repo)

    assert proactive.detect_nudge("tenant-1") == NUDGE_NO_POST

    repo.record_published_content(
        tenant_id="tenant-1",
        workflow_type="kachu_google_post",
        channel="google_business",
        source_id="run-1",
        source_ref="post-1",
        content_text="本週新品",
        payload={},
    )
    repo.save_pending_approval(
        tenant_id="tenant-1",
        agentos_task_id="task-1",
        agentos_run_id="run-2",
        workflow_type="kachu_review_reply",
        draft_content='{"sentiment": {"sentiment": "negative"}}',
        review_id="review-1",
    )
    assert proactive.detect_nudge("tenant-1") == NUDGE_NEGATIVE_REVIEW

    repo.decide_pending_approval(agentos_run_id="run-2", decision="approved", actor_line_id="owner")
    with Session(repo._engine) as session:  # noqa: SLF001
        latest = session.exec(
            select(KnowledgeEntryTable).where(KnowledgeEntryTable.tenant_id == "tenant-1")
        ).first()
        assert latest is not None
        latest.created_at = datetime.now(timezone.utc) - timedelta(days=61)
        session.add(latest)
        session.commit()
    assert proactive.detect_nudge("tenant-1") == NUDGE_STALE_KNOWLEDGE

    repo.save_knowledge_entry(tenant_id="tenant-1", category="basic_info", content="五月新菜單已更新")
    with Session(repo._engine) as session:  # noqa: SLF001
        session.add(
            CustomerProfileTable(
                tenant_id="tenant-1",
                display_name="老顧客",
                last_interaction_at=datetime.now(timezone.utc) - timedelta(days=40),
                sleep_since_days=40,
                status="sleeping",
            )
        )
        session.commit()
    assert proactive.detect_nudge("tenant-1") == NUDGE_SLEEPING_CUSTOMERS

    created = proactive.run_once_for_tenant("tenant-1")
    assert created is not None
    assert created["suggestion_type"] == NUDGE_SLEEPING_CUSTOMERS


def test_policy_resolver_high_and_low_trust() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    resolver = KachuExecutionPolicyResolver(repo)

    for index in range(3):
        run_id = f"run-high-{index}"
        repo.save_pending_approval(
            tenant_id="tenant-1",
            agentos_task_id=f"task-high-{index}",
            agentos_run_id=run_id,
            workflow_type="kachu_google_post",
            draft_content='{"post_text": "原始貼文"}',
        )
        repo.decide_pending_approval(
            agentos_run_id=run_id,
            decision="approved",
            actor_line_id="owner",
            decision_payload={"post_text": "原始貼文"},
        )
    repo.compute_and_save_approval_profile("tenant-1")

    high_trust = resolver.resolve("tenant-1")
    assert high_trust["approval_timeout_seconds"] == 21600
    assert high_trust["require_direction_check"] is False

    engine_low = _make_engine()
    SQLModel.metadata.create_all(engine_low)
    repo_low = KachuPlusRepository(engine_low)
    _seed_tenant(repo_low, tenant_id="tenant-2")
    resolver_low = KachuExecutionPolicyResolver(repo_low)
    for index in range(3):
        run_id = f"run-low-{index}"
        repo_low.save_pending_approval(
            tenant_id="tenant-2",
            agentos_task_id=f"task-low-{index}",
            agentos_run_id=run_id,
            workflow_type="kachu_google_post",
            draft_content='{"post_text": "原始貼文"}',
        )
        repo_low.decide_pending_approval(
            agentos_run_id=run_id,
            decision="rejected",
            actor_line_id="owner",
            decision_payload={"post_text": "大改版貼文"},
        )
    repo_low.compute_and_save_approval_profile("tenant-2")

    low_trust = resolver_low.resolve("tenant-2")
    assert low_trust["require_direction_check"] is True
    assert "避免制式語氣" in low_trust["policy_generation_context"]


def test_learning_and_brief_refresh_persists_examples() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    settings = Settings()
    memory = MemoryManager(repo, settings)
    briefs = ContextBriefManager(repo, memory)
    review = PostTaskReviewService(repo, memory, briefs)

    memory.store_preference(
        tenant_id="tenant-1",
        platform="google",
        original_draft="原始貼文",
        edited_draft="修正版貼文！",
        run_id="run-1",
    )
    memory.record_episode(
        tenant_id="tenant-1",
        workflow_type="kachu_google_post",
        outcome="approved",
        context_summary={"run_id": "run-1"},
    )

    asyncio.run(review.after_approval_decision(
        tenant_id="tenant-1",
        workflow_type="kachu_google_post",
        outcome="approved",
        context_summary={"run_id": "run-1"},
    ))

    owner_brief = repo.get_context_brief("tenant-1", "owner_brief")
    customer_brief = repo.get_context_brief("tenant-1", "customer_brief")
    assert owner_brief is not None
    assert customer_brief is not None
    examples = memory.get_preference_examples("tenant-1", "google", limit=3)
    assert len(examples) == 1
    assert examples[0]["edited"] == "修正版貼文！"


def test_proactive_engine_pushes_to_owner_membership() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-owner-1", role="owner")

    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "push-token"

    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    import kachu_plus.proactive as proactive_module

    original_push = proactive_module.push_line_messages
    proactive_module.push_line_messages = _fake_push
    try:
        proactive = ProactiveSuggestionEngine(repo, settings)
        created = proactive.run_once_for_tenant("tenant-1")
    finally:
        proactive_module.push_line_messages = original_push

    assert created is not None
    assert len(pushed) == 1
    assert pushed[0]["to"] == "U-owner-1"
    assert pushed[0]["messages"][0]["type"] == "flex"


def test_proactive_engine_persists_card_fields_and_reuses_active_suggestion() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)

    proactive = ProactiveSuggestionEngine(repo)
    created = proactive.run_once_for_tenant("tenant-1")
    repeated = proactive.run_once_for_tenant("tenant-1")

    assert created is not None
    assert repeated is not None
    assert repeated["id"] == created["id"]
    assert created["suggestion_type"] == NUDGE_NO_POST
    assert created["category"] == "brand_presence"
    assert created["reason"]
    assert created["suggested_action"]
    assert created["draft_message"]
    suggestions = repo.list_pending_suggestions("tenant-1")
    assert len(suggestions) == 1
    assert suggestions[0].status == "pending"
    assert suggestions[0].expires_at is not None


def test_proactive_scheduler_uses_durable_job_state_across_restart() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)

    scheduler = ProactiveSuggestionScheduler(ProactiveSuggestionEngine(repo), interval_seconds=300)
    first = asyncio.run(scheduler.run_once())
    assert "tenant-1" in first

    job = repo.get_recurring_job("tenant-1", PROACTIVE_SCAN_JOB)
    assert job is not None
    assert job.last_run_at is not None

    restarted = ProactiveSuggestionScheduler(ProactiveSuggestionEngine(repo), interval_seconds=300)
    second = asyncio.run(restarted.run_once())
    assert second == {}

    with Session(repo._engine) as session:  # noqa: SLF001
        stored_job = session.get(type(job), job.id)
        stored_job.next_run_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        stored_suggestion = session.get(SuggestionTable, first["tenant-1"]["id"])
        stored_suggestion.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.add(stored_job)
        session.add(stored_suggestion)
        session.commit()

    third = asyncio.run(restarted.run_once())
    assert "tenant-1" in third
    assert third["tenant-1"]["id"] != first["tenant-1"]["id"]
    refreshed_job = repo.get_recurring_job("tenant-1", PROACTIVE_SCAN_JOB)
    assert refreshed_job is not None
    assert refreshed_job.last_run_at is not None


def test_proactive_engine_sends_only_one_due_reminder() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-owner-1", role="owner")

    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "push-token"
    suggestion = repo.create_suggestion(
        tenant_id="tenant-1",
        suggestion_type=NUDGE_NO_POST,
        category="brand_presence",
        title="最近 7 天沒有新貼文",
        reason="品牌曝光正在下降",
        body="建議補一篇內容",
        suggested_action="發一篇本週 Google 商家動態",
        draft_message="這週新品已上架，歡迎回來看看。",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=6),
    )

    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    import kachu_plus.proactive as proactive_module

    original_push = proactive_module.push_line_messages
    proactive_module.push_line_messages = _fake_push
    try:
        proactive = ProactiveSuggestionEngine(repo, settings)
        first = proactive.send_due_reminders_for_tenant("tenant-1")
        second = proactive.send_due_reminders_for_tenant("tenant-1")
    finally:
        proactive_module.push_line_messages = original_push

    assert first == 1
    assert second == 0
    assert len(pushed) == 1
    stored = repo.get_suggestion(suggestion.id)
    assert stored is not None
    assert json.loads(stored.result_snapshot_json or "{}")["reminder_count"] == 1


def test_proactive_scheduler_sends_daily_meta_report_via_durable_job() -> None:
    engine = _make_engine()
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    _seed_tenant(repo)
    repo.create_tenant_membership(tenant_id="tenant-1", line_user_id="U-owner-1", role="owner")

    settings = Settings()
    settings.LINE_CHANNEL_ACCESS_TOKEN = "push-token"

    meta_service = MagicMock()
    meta_service.fetch_insights.return_value = {
        "status": "ok",
        "period": "week",
        "facebook_page_insights": {"page_impressions_unique": 1800, "page_post_engagements": 240},
        "facebook_post_insights": {"post_engagements": 72},
        "instagram_media_insights": {"reach": 260},
    }
    consultant = MagicMock()
    consultant.build_reply = AsyncMock(return_value="這週 Meta 觸及穩定，建議把高互動貼文加上更清楚的預約 CTA。")

    pushed: list[dict] = []

    async def _fake_push(*, to: str, messages, access_token: str) -> None:
        pushed.append({"to": to, "messages": messages, "access_token": access_token})

    import kachu_plus.meta as meta_module

    original_push = meta_module.push_line_messages
    meta_module.push_line_messages = _fake_push
    try:
        scheduler = ProactiveSuggestionScheduler(
            ProactiveSuggestionEngine(
                repo,
                settings,
                consultant=consultant,
                meta_service=meta_service,
            ),
            interval_seconds=300,
        )
        asyncio.run(scheduler.run_once())
        asyncio.run(scheduler.run_once())
    finally:
        meta_module.push_line_messages = original_push

    assert len(pushed) == 1
    assert pushed[0]["to"] == "U-owner-1"
    assert pushed[0]["messages"][0]["type"] == "flex"
    job = repo.get_recurring_job("tenant-1", META_INSIGHTS_REPORT_JOB)
    assert job is not None
    assert job.last_run_at is not None
    assert json.loads(job.last_result_json or "{}") ["status"] == "sent"