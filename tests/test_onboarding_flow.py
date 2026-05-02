from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel

from kachu.config import Settings
from kachu.context_brief_manager import ContextBriefManager
from kachu.main import create_app
from kachu.memory import MemoryManager
from kachu.onboarding.flow import OnboardingFlow
from kachu.persistence import KachuRepository, create_db_engine, init_db


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def repo() -> KachuRepository:
    engine = create_db_engine("sqlite://")
    init_db(engine)
    return KachuRepository(engine)


@pytest.fixture()
def intent_router() -> AsyncMock:
    router = AsyncMock()
    router.dispatch = AsyncMock()
    return router


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        APP_ENV="test",
        DATABASE_URL="sqlite://",
        KACHU_BASE_URL="https://app.kachu.tw",
    )


@pytest.fixture()
def flow(repo: KachuRepository, intent_router: AsyncMock, settings: Settings) -> OnboardingFlow:
    memory = MemoryManager(repo, settings)
    brief_manager = ContextBriefManager(repo, memory)
    return OnboardingFlow(
        repo,
        settings=settings,
        intent_router=intent_router,
        memory_manager=memory,
        context_brief_manager=brief_manager,
    )


TENANT = "U_boss_test_001"


# ── is_in_onboarding ──────────────────────────────────────────────────────────

def test_new_tenant_is_in_onboarding(flow: OnboardingFlow) -> None:
    assert flow.is_in_onboarding(TENANT) is True


def test_completed_tenant_not_in_onboarding(flow: OnboardingFlow, repo: KachuRepository) -> None:
    repo.update_onboarding_state(TENANT, "completed")
    assert flow.is_in_onboarding(TENANT) is False


# ── Full DAY 0 happy-path ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_welcome_message_on_first_contact(flow: OnboardingFlow) -> None:
    msgs = await flow.handle_message(TENANT, "text", "hi")
    assert len(msgs) == 1
    assert "歡迎" in msgs[0]["text"]
    assert "店名" in msgs[0]["text"]


@pytest.mark.asyncio
async def test_collecting_name(flow: OnboardingFlow, repo: KachuRepository) -> None:
    # Advance to asking_name state
    await flow.handle_message(TENANT, "text", "hi")

    msgs = await flow.handle_message(TENANT, "text", "小王美食")
    assert "小王美食" in msgs[0]["text"]
    assert "行業" in msgs[0]["text"]

    tenant = repo.get_or_create_tenant(TENANT)
    assert tenant.name == "小王美食"


@pytest.mark.asyncio
async def test_collecting_industry(flow: OnboardingFlow, repo: KachuRepository) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")

    msgs = await flow.handle_message(TENANT, "text", "餐廳")
    assert "地址" in msgs[0]["text"]

    tenant = repo.get_or_create_tenant(TENANT)
    assert tenant.industry_type == "餐廳"


@pytest.mark.asyncio
async def test_collecting_address(flow: OnboardingFlow, repo: KachuRepository) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")

    msgs = await flow.handle_message(TENANT, "text", "台北市信義區")
    assert "完成" in msgs[0]["text"] or "文件" in msgs[0]["text"]

    tenant = repo.get_or_create_tenant(TENANT)
    assert tenant.address == "台北市信義區"


@pytest.mark.asyncio
async def test_awaiting_docs_skip(flow: OnboardingFlow) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")
    await flow.handle_message(TENANT, "text", "台北市信義區")

    msgs = await flow.handle_message(TENANT, "text", "跳過")
    assert "第 1 題" in msgs[0]["text"]


@pytest.mark.asyncio
async def test_awaiting_docs_image_upload(
    flow: OnboardingFlow, repo: KachuRepository
) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")
    await flow.handle_message(TENANT, "text", "台北市信義區")

    msgs = await flow.handle_message(TENANT, "image", "msg_id_001")
    assert "收到" in msgs[0]["text"]
    assert any("我目前已先吸收這些資訊" in message["text"] for message in msgs)

    # Check knowledge entry saved
    entries = repo.get_knowledge_entries(TENANT, category="document")
    assert len(entries) == 1
    assert "msg_id_001" in entries[0].content


@pytest.mark.asyncio
async def test_awaiting_docs_done_keyword(flow: OnboardingFlow) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")
    await flow.handle_message(TENANT, "text", "台北市信義區")

    msgs = await flow.handle_message(TENANT, "text", "完成")
    assert "第 1 題" in msgs[0]["text"]


@pytest.mark.asyncio
async def test_full_interview_creates_knowledge_entries(
    flow: OnboardingFlow, repo: KachuRepository, intent_router: AsyncMock
) -> None:
    # Run entire flow
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")
    await flow.handle_message(TENANT, "text", "台北市信義區")
    await flow.handle_message(TENANT, "text", "跳過")
    await flow.handle_message(TENANT, "text", "我們用祖傳秘方，別家沒有")
    await flow.handle_message(TENANT, "text", "客人太少，不知道怎麼宣傳")
    repo.save_connector_account(
        tenant_id=TENANT,
        platform="meta",
        credentials_json=json.dumps(
            {
                "access_token": "meta-token",
                "fb_page_id": "fb-page-001",
                "fb_page_name": "小王美食粉專",
                "ig_user_id": "",
            }
        ),
        account_label="Meta (小王美食粉專)",
    )

    msgs = await flow.handle_message(TENANT, "text", "今年想開第二家店")
    assert len(msgs) == 3
    assert "我目前已先吸收這些資訊" in msgs[0]["text"]
    assert "目前渠道狀態" in msgs[1]["text"]
    assert "Facebook：已可用" in msgs[1]["text"]
    assert "太好了" in msgs[2]["text"] or "了解" in msgs[2]["text"] or "照片" in msgs[2]["text"]

    # Verify knowledge entries
    core = repo.get_knowledge_entries(TENANT, category="core_value")
    pain = repo.get_knowledge_entries(TENANT, category="pain_point")
    goal = repo.get_knowledge_entries(TENANT, category="goal")
    basic = repo.get_knowledge_entries(TENANT, category="basic_info")

    assert len(core) == 1
    assert "祖傳秘方" in core[0].content
    assert len(pain) == 1
    assert "客人太少" in pain[0].content
    assert len(goal) == 1
    assert "第二家" in goal[0].content
    assert len(basic) == 1
    assert "小王美食" in basic[0].content
    brand_brief = repo.get_shared_context(TENANT, "brand_brief")
    assert brand_brief is not None
    assert brand_brief["brand_name"] == "小王美食"
    assert intent_router.dispatch.await_count == 3

    topics = [
        call.kwargs["trigger_payload"]["topic"]
        for call in intent_router.dispatch.await_args_list
    ]
    assert all(call.kwargs["trigger_source"] == "onboarding_aha" for call in intent_router.dispatch.await_args_list)
    assert topics == [
        "認識小王美食：第一次來店前最值得知道的亮點",
        "為什麼大家會選擇小王美食：主打特色與推薦理由",
        "這週想讓更多人知道的餐廳亮點：來店前先看這篇",
    ]


@pytest.mark.asyncio
async def test_completed_flow_is_no_longer_onboarding(
    flow: OnboardingFlow,
) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")
    await flow.handle_message(TENANT, "text", "台北市信義區")
    await flow.handle_message(TENANT, "text", "跳過")
    await flow.handle_message(TENANT, "text", "祖傳秘方")
    await flow.handle_message(TENANT, "text", "客人太少")
    await flow.handle_message(TENANT, "text", "開第二家")

    assert flow.is_in_onboarding(TENANT) is False


@pytest.mark.asyncio
async def test_completed_flow_returns_empty_messages(
    flow: OnboardingFlow,
) -> None:
    """After completion, further messages return nothing from onboarding."""
    repo_inner = flow._repo
    repo_inner.update_onboarding_state(TENANT, "completed")

    msgs = await flow.handle_message(TENANT, "text", "隨便說什麼")
    assert msgs == []


@pytest.mark.asyncio
async def test_conversations_saved_during_interview(
    flow: OnboardingFlow, repo: KachuRepository
) -> None:
    await flow.handle_message(TENANT, "text", "hi")
    await flow.handle_message(TENANT, "text", "小王美食")
    await flow.handle_message(TENANT, "text", "餐廳")
    await flow.handle_message(TENANT, "text", "台北市信義區")
    await flow.handle_message(TENANT, "text", "跳過")
    await flow.handle_message(TENANT, "text", "我的獨特性")
    await flow.handle_message(TENANT, "text", "我的困擾")
    await flow.handle_message(TENANT, "text", "我的目標")

    # Check conversations saved (name + industry + address + 3 interview answers)
    convs = repo.save_conversation.__self__  # just check via get
    # Use select directly on tables
    from sqlmodel import Session, select
    from kachu.persistence.tables import ConversationTable
    with Session(repo._engine) as session:
        results = list(session.exec(
            select(ConversationTable).where(ConversationTable.tenant_id == TENANT)
        ).all())
    # name, industry, address, q1, q2, q3 = 6 boss messages
    boss_msgs = [r for r in results if r.role == "boss"]
    assert len(boss_msgs) == 6
