from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlmodel import SQLModel, Session, create_engine, select

from kachu_plus.agentos_client import AgentOSWorkflowClient
from kachu_plus.config import Settings
from kachu_plus.persistence.repository import KachuPlusRepository
from kachu_plus.persistence.tables import ChannelEntityTable, CustomerProfileTable, ProfileLinkTable, TenantTable
from kachu_plus.services import (
    AgentOSTaskDispatcher,
    SleepCustomerQueryService,
    SleepProfileSyncService,
    SleepSyncScheduler,
    LLMConsultant,
    UnsupportedExecutionIntentError,
    _build_agentos_task_request,
)


def test_build_agentos_task_request_for_google_post() -> None:
    payload = _build_agentos_task_request(
        tenant_id="tenant-1",
        text="幫我寫一篇母親節貼文",
        intent_label="google_post",
    )
    assert payload["domain"] == "kachu_google_post"
    assert payload["workflow_input"]["tenant_id"] == "tenant-1"
    assert "母親節" in payload["workflow_input"]["topic"]
    assert payload["idempotency_key"].startswith("boss:google_post:")


def test_build_agentos_task_request_for_knowledge_update() -> None:
    payload = _build_agentos_task_request(
        tenant_id="tenant-1",
        text="幫我更新營業時間到晚上九點",
        intent_label="business_profile_update",
    )
    assert payload["domain"] == "kachu_knowledge_update"
    assert payload["workflow_input"]["boss_message"] == "幫我更新營業時間到晚上九點"


def test_build_agentos_task_request_rejects_unsupported_intent() -> None:
    with pytest.raises(UnsupportedExecutionIntentError):
        _build_agentos_task_request(
            tenant_id="tenant-1",
            text="哪些客人超過 60 天沒來",
            intent_label="sleep_customer_query",
        )


@pytest.mark.asyncio
async def test_agentos_task_dispatcher_auto_run_tracks_run_status() -> None:
    fake_client = MagicMock(spec=AgentOSWorkflowClient)
    fake_client.create_task.return_value = MagicMock(
        task={"id": "task-1", "status": "created", "current_run_id": None},
        plan={"id": "plan-1"},
    )
    fake_client.run_task.return_value = MagicMock(
        run={"id": "run-1", "task_id": "task-1", "status": "waiting_approval"},
        run_state={"summary": "waiting approval"},
        approvals=[{"id": "approval-1", "decision": "pending"}],
        checkpoints=[],
    )
    dispatcher = AgentOSTaskDispatcher(
        Settings(AGENTOS_AUTO_RUN_EXECUTE_TASKS=True),
        client=fake_client,
    )

    result = await dispatcher.dispatch(
        tenant_id="tenant-1",
        text="幫我寫一篇母親節貼文",
        intent_label="google_post",
    )

    assert result.task_id == "task-1"
    assert result.current_run_id == "run-1"
    assert result.status == "waiting_approval"
    assert result.waiting_approval is True
    assert result.approval_count == 1
    fake_client.create_task.assert_awaited_once()
    fake_client.run_task.assert_awaited_once_with("task-1")


@pytest.mark.asyncio
async def test_agentos_task_dispatcher_without_auto_run_keeps_created_task() -> None:
    fake_client = MagicMock(spec=AgentOSWorkflowClient)
    fake_client.create_task.return_value = MagicMock(
        task={"id": "task-1", "status": "created", "current_run_id": None},
        plan={"id": "plan-1"},
    )
    dispatcher = AgentOSTaskDispatcher(
        Settings(AGENTOS_AUTO_RUN_EXECUTE_TASKS=False),
        client=fake_client,
    )

    result = await dispatcher.dispatch(
        tenant_id="tenant-1",
        text="幫我回覆評論",
        intent_label="review_reply",
    )

    assert result.task_id == "task-1"
    assert result.current_run_id is None
    assert result.status == "created"
    assert result.waiting_approval is False
    assert result.approval_count == 0
    fake_client.create_task.assert_awaited_once()
    fake_client.run_task.assert_not_called()


@pytest.mark.asyncio
async def test_llm_consultant_fallback_reply_without_api_keys() -> None:
    consultant = LLMConsultant(Settings())
    reply = await consultant.build_reply(
        tenant_name="測試店",
        industry_type="咖啡廳",
        message="你覺得我要怎麼提升回購率？",
        context_bundle={"relevant_knowledge": ["主打社區熟客與手沖咖啡"]},
    )
    assert "測試店" in reply
    assert "優先順序" in reply


def test_sleep_customer_query_service_uses_tenant_threshold_by_default() -> None:
    repo = MagicMock()
    repo.get_tenant.return_value = TenantTable(id="tenant-1", name="店家", sleep_threshold=45)
    repo.list_sleeping_customer_profiles.return_value = []
    service = SleepCustomerQueryService(repo)

    reply = service.build_reply(tenant_id="tenant-1", text="沉睡顧客有哪些")

    assert "45 天" in reply
    repo.list_sleeping_customer_profiles.assert_called_once_with(
        "tenant-1",
        minimum_days=45,
        limit=10,
    )


def test_sleep_customer_query_service_formats_customer_list() -> None:
    repo = MagicMock()
    repo.get_tenant.return_value = TenantTable(id="tenant-1", name="店家", sleep_threshold=30)
    repo.list_sleeping_customer_profiles.return_value = [
        CustomerProfileTable(id="p1", tenant_id="tenant-1", custom_name="VIP 王小姐", sleep_since_days=91),
        CustomerProfileTable(id="p2", tenant_id="tenant-1", display_name="陳先生", sleep_since_days=75),
    ]
    service = SleepCustomerQueryService(repo)

    reply = service.build_reply(tenant_id="tenant-1", text="哪些客人超過60天沒來")

    assert "2 位顧客" in reply
    assert "VIP 王小姐" in reply
    assert "陳先生" in reply


def test_repository_list_sleeping_customer_profiles_excludes_blacklisted_and_opt_out() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)

    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.add(CustomerProfileTable(id="p1", tenant_id="tenant-1", display_name="可聯絡", sleep_since_days=70, status="sleeping"))
        session.add(CustomerProfileTable(id="p2", tenant_id="tenant-1", display_name="退訂", sleep_since_days=80, status="sleeping", opt_out=True))
        session.add(CustomerProfileTable(id="p3", tenant_id="tenant-1", display_name="黑名單", sleep_since_days=90, status="blacklisted"))
        session.commit()

    rows = repo.list_sleeping_customer_profiles("tenant-1", minimum_days=60)

    assert [row.id for row in rows] == ["p1"]


def test_repository_resolve_or_create_line_profile_reuses_existing_profile() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)

    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    first = repo.resolve_or_create_line_profile("tenant-1", "U123")
    second = repo.resolve_or_create_line_profile("tenant-1", "U123")

    assert first.id == second.id
    assert repo.count_customer_profiles("tenant-1") == 1

    with Session(engine) as session:
        entities = list(session.exec(select(ChannelEntityTable)).all())
        links = list(session.exec(select(ProfileLinkTable)).all())
        profile = session.get(CustomerProfileTable, first.id)

    assert len(entities) == 1
    assert len(links) == 1
    assert profile is not None
    assert profile.interaction_count == 2


def test_repository_can_save_and_query_recent_conversations() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)

    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    first = repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U123",
        actor_role="boss",
        channel_type="line",
        conversation_kind="boss_command",
        content_text="幫我寫一篇貼文",
        source_message_id="m-1",
    )
    repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U123",
        actor_role="ai",
        channel_type="line",
        conversation_kind="execute_ack",
        content_text="好的，我幫你處理中。",
        related_task_id="task-1",
        metadata={"source_conversation_id": first.id},
    )

    rows = repo.list_recent_conversations("tenant-1", limit=5, line_user_id="U123")

    assert len(rows) == 2
    assert rows[0].actor_role == "ai"
    assert rows[1].actor_role == "boss"


def test_repository_can_query_related_conversations_and_memory_traceability() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)

    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="店家"))
        session.commit()

    conversation = repo.save_conversation(
        tenant_id="tenant-1",
        line_user_id="U123",
        actor_role="boss",
        channel_type="line",
        conversation_kind="boss_command",
        content_text="我們主打漢方保健",
        related_task_id="task-1",
        related_run_id="run-1",
    )
    repo.save_execute_task_record(
        tenant_id="tenant-1",
        line_user_id="U123",
        intent_label="google_post",
        source_text="幫我寫一篇貼文",
        objective="Create a Google post draft",
        task_id="task-1",
        run_id="run-1",
        related_conversation_id=conversation.id,
    )
    repo.save_knowledge_entry(
        tenant_id="tenant-1",
        category="core_value",
        content="主打漢方保健",
        source_conversation_id=conversation.id,
        confidence_score=0.9,
    )

    related_rows = repo.list_related_conversations("tenant-1", related_task_id="task-1")
    task_record = repo.get_latest_execute_task_for_tenant("tenant-1")
    knowledge_rows = repo.list_knowledge_entries("tenant-1", limit=5)

    assert len(related_rows) == 1
    assert related_rows[0].id == conversation.id
    assert task_record is not None and task_record.related_conversation_id == conversation.id
    assert knowledge_rows[0].source_conversation_id == conversation.id
    assert knowledge_rows[0].confidence_score == pytest.approx(0.9)


def test_sleep_profile_sync_service_updates_days_and_status() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    now = datetime(2026, 5, 9, tzinfo=timezone.utc)

    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="店家", sleep_threshold=30))
        session.add(
            CustomerProfileTable(
                id="p1",
                tenant_id="tenant-1",
                display_name="睡眠客",
                last_interaction_at=now - timedelta(days=45),
                status="active",
            )
        )
        session.add(
            CustomerProfileTable(
                id="p2",
                tenant_id="tenant-1",
                display_name="活躍客",
                last_interaction_at=now - timedelta(days=7),
                status="sleeping",
            )
        )
        session.commit()

    service = SleepProfileSyncService(repo)
    updated = service.sync_tenant("tenant-1", now=now)

    assert updated == 2
    sleepy = repo.get_customer_profile("p1")
    active = repo.get_customer_profile("p2")
    assert sleepy is not None and sleepy.sleep_since_days == 45 and sleepy.status == "sleeping"
    assert active is not None and active.sleep_since_days == 7 and active.status == "active"


def test_sleep_profile_sync_service_preserves_blacklisted_status() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    now = datetime(2026, 5, 9, tzinfo=timezone.utc)

    with Session(engine) as session:
        session.add(TenantTable(id="tenant-1", name="店家", sleep_threshold=30))
        session.add(
            CustomerProfileTable(
                id="p1",
                tenant_id="tenant-1",
                display_name="黑名單客",
                last_interaction_at=now - timedelta(days=90),
                status="blacklisted",
            )
        )
        session.commit()

    service = SleepProfileSyncService(repo)
    service.sync_tenant("tenant-1", now=now)

    profile = repo.get_customer_profile("p1")
    assert profile is not None
    assert profile.sleep_since_days == 90
    assert profile.status == "blacklisted"


def test_sleep_profile_sync_service_sync_all_tenants_only_active() -> None:
    engine = create_engine("sqlite://")
    SQLModel.metadata.create_all(engine)
    repo = KachuPlusRepository(engine)
    now = datetime(2026, 5, 9, tzinfo=timezone.utc)

    with Session(engine) as session:
        session.add(TenantTable(id="active-tenant", name="A", is_active=True, sleep_threshold=30))
        session.add(TenantTable(id="inactive-tenant", name="B", is_active=False, sleep_threshold=30))
        session.add(CustomerProfileTable(id="p1", tenant_id="active-tenant", last_interaction_at=now - timedelta(days=31)))
        session.add(CustomerProfileTable(id="p2", tenant_id="inactive-tenant", last_interaction_at=now - timedelta(days=31)))
        session.commit()

    service = SleepProfileSyncService(repo)
    summary = service.sync_all_tenants(now=now)

    assert summary == {"active-tenant": 1}
    active_profile = repo.get_customer_profile("p1")
    inactive_profile = repo.get_customer_profile("p2")
    assert active_profile is not None and active_profile.sleep_since_days == 31
    assert inactive_profile is not None and inactive_profile.sleep_since_days == 0


@pytest.mark.asyncio
async def test_sleep_sync_scheduler_run_once_calls_service() -> None:
    service = MagicMock()
    service.sync_all_tenants.return_value = {"tenant-1": 3}
    scheduler = SleepSyncScheduler(
        service,
        Settings(SLEEP_SYNC_ENABLED=True, SLEEP_SYNC_RUN_ON_STARTUP=False, SLEEP_SYNC_INTERVAL_SECONDS=3600),
    )

    result = await scheduler.run_once()

    assert result == {"tenant-1": 3}
    service.sync_all_tenants.assert_called_once_with()