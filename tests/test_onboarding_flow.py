"""
任務 1-4 驗收測試：Onboarding flow。

完成條件：
- 完整走完 6 steps（name→industry→sleep_threshold→address→docs→interview）
- 「上一題」正確回退，且不遺失已存資料
- sleep_threshold 正確解析並儲存到 tenant
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, call

from kachu_plus.onboarding.flow import OnboardingFlow, _parse_sleep_threshold, _detect_redo_step
from kachu_plus.persistence.tables import TenantTable, OnboardingStateTable
from kachu_plus.website_knowledge import WebsiteKnowledgeResult


def _make_tenant(tenant_id: str = "t1") -> TenantTable:
    return TenantTable(
        id=tenant_id,
        name="",
        industry_type="",
        address="",
        sleep_threshold=60,
        is_active=True,
    )


def _make_state(step: str = "new", tenant_id: str = "t1") -> OnboardingStateTable:
    return OnboardingStateTable(id="s1", tenant_id=tenant_id, step=step)


def _make_repo(step: str = "new") -> MagicMock:
    repo = MagicMock()
    tenant = _make_tenant()
    state = _make_state(step)
    repo.get_tenant.return_value = tenant
    repo.get_or_create_onboarding_state.return_value = state
    repo.is_onboarding_complete.return_value = (step == "completed")
    repo.get_knowledge_entries.return_value = []
    return repo


# ── _parse_sleep_threshold ────────────────────────────────────────────────────


@pytest.mark.parametrize("text,expected_days", [
    ("每週", 7),
    ("每月", 30),
    ("每兩週", 14),
    ("每天", 1),
    ("半個月", 15),
    ("每兩個月", 60),
    ("30", 30),
    ("45天", 45),
    ("無法辨認的文字", 30),  # default
    ("每周来一次", 7),
])
def test_parse_sleep_threshold(text: str, expected_days: int) -> None:
    assert _parse_sleep_threshold(text) == expected_days


# ── _detect_redo_step ─────────────────────────────────────────────────────────


def test_detect_redo_from_q2_generic() -> None:
    assert _detect_redo_step("上一題", "interview_q2") == "interview_q1"


def test_detect_redo_from_q3_to_q2() -> None:
    assert _detect_redo_step("上一題", "interview_q3") == "interview_q2"


def test_detect_redo_explicit_q1_from_q3() -> None:
    assert _detect_redo_step("回到第一題", "interview_q3") == "interview_q1"


def test_detect_redo_explicit_q2_from_q3() -> None:
    assert _detect_redo_step("第二題要重新回答", "interview_q3") == "interview_q2"


def test_no_redo_signal() -> None:
    assert _detect_redo_step("我覺得我們的差異是手工製作", "interview_q2") is None


# ── OnboardingFlow 完整流程 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_new_step_sends_welcome() -> None:
    repo = _make_repo("new")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "")
    assert len(replies) == 1
    assert "歡迎" in replies[0]["text"]
    repo.update_onboarding_step.assert_called_once_with("t1", "asking_name")


@pytest.mark.asyncio
async def test_asking_name_saves_and_advances() -> None:
    repo = _make_repo("asking_name")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "好味咖啡")
    assert len(replies) == 1
    assert "好味咖啡" in replies[0]["text"]
    # tenant name was saved
    tenant = repo.get_tenant.return_value
    assert tenant.name == "好味咖啡"
    repo.update_onboarding_step.assert_called_once_with("t1", "asking_industry")


@pytest.mark.asyncio
async def test_asking_name_rejects_non_text_input() -> None:
    repo = _make_repo("asking_name")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "image", "")
    assert "請直接用文字" in replies[0]["text"]
    repo.update_onboarding_step.assert_not_called()
    repo.save_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_asking_industry_rejects_empty_text() -> None:
    repo = _make_repo("asking_industry")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "   ")
    assert "請直接用文字" in replies[0]["text"]
    repo.update_onboarding_step.assert_not_called()
    repo.save_tenant.assert_not_called()


@pytest.mark.asyncio
async def test_asking_industry_advances_to_sleep_threshold() -> None:
    repo = _make_repo("asking_industry")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "咖啡廳")
    assert len(replies) == 1
    # 應進入 sleep_threshold 問題
    assert "幾天" in replies[0]["text"] or "客人" in replies[0]["text"]
    repo.update_onboarding_step.assert_called_once_with("t1", "asking_sleep_threshold")


@pytest.mark.asyncio
async def test_sleep_threshold_saves_and_advances() -> None:
    repo = _make_repo("asking_sleep_threshold")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "每月")
    assert len(replies) == 1
    # 應詢問地址
    assert "地址" in replies[0]["text"] or "地點" in replies[0]["text"]
    tenant = repo.get_tenant.return_value
    assert tenant.sleep_threshold == 30
    repo.update_onboarding_step.assert_called_once_with("t1", "asking_address")


@pytest.mark.asyncio
async def test_asking_address_advances() -> None:
    repo = _make_repo("asking_address")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "台北市信義區忠孝東路100號")
    assert len(replies) == 1
    repo.update_onboarding_step.assert_called_once_with("t1", "awaiting_docs")


@pytest.mark.asyncio
async def test_awaiting_docs_skip_advances_to_interview() -> None:
    repo = _make_repo("awaiting_docs")
    flow = OnboardingFlow(repo)
    for skip_word in ("跳過", "skip", "完成"):
        repo.update_onboarding_step.reset_mock()
        replies = await flow.handle_message("t1", "text", skip_word)
        assert "第 1 題" in replies[0]["text"]
        repo.update_onboarding_step.assert_called_once_with("t1", "interview_q1")


@pytest.mark.asyncio
async def test_awaiting_docs_text_saved_as_knowledge() -> None:
    repo = _make_repo("awaiting_docs")
    flow = OnboardingFlow(repo)
    await flow.handle_message("t1", "text", "我們主打有機食材")
    repo.save_knowledge_entry.assert_called_once()
    call_kwargs = repo.save_knowledge_entry.call_args.kwargs
    assert call_kwargs["category"] == "brand_material"
    assert "有機食材" in call_kwargs["content"]


@pytest.mark.asyncio
async def test_awaiting_docs_url_triggers_website_ingestion() -> None:
    repo = _make_repo("awaiting_docs")
    website_ingestion_service = MagicMock()
    website_ingestion_service.ingest_from_message = AsyncMock(return_value=WebsiteKnowledgeResult(
        source_url="https://seasonwell.com.tw",
        brand_name="四時循養堂",
        summary="主打漢方保健與日常調理。",
        highlights=["筋骨保養", "漢方保健食品"],
        contact_points=["地址資訊：新北市泰山區仁義路222號"],
        page_urls=["https://seasonwell.com.tw"],
    ))
    flow = OnboardingFlow(repo, website_ingestion_service=website_ingestion_service)

    replies = await flow.handle_message("t1", "text", "給你官網可以嗎？ https://seasonwell.com.tw/")

    assert "官網重點" in replies[0]["text"]
    assert "四時循養堂" in replies[0]["text"]
    website_ingestion_service.ingest_from_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_interview_q1_saves_core_value() -> None:
    repo = _make_repo("interview_q1")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "手工製作，堅持每日新鮮")
    assert "第 2 題" in replies[0]["text"]
    repo.save_knowledge_entry.assert_called_once()
    call_kwargs = repo.save_knowledge_entry.call_args.kwargs
    assert call_kwargs["category"] == "core_value"
    repo.update_onboarding_step.assert_called_once_with("t1", "interview_q2")


@pytest.mark.asyncio
async def test_interview_q2_saves_pain_point() -> None:
    repo = _make_repo("interview_q2")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "老客人越來越少回來")
    assert "第 3 題" in replies[0]["text"]
    repo.save_knowledge_entry.assert_called_once()
    call_kwargs = repo.save_knowledge_entry.call_args.kwargs
    assert call_kwargs["category"] == "pain_point"
    repo.update_onboarding_step.assert_called_once_with("t1", "interview_q3")


@pytest.mark.asyncio
async def test_interview_q3_completes_onboarding() -> None:
    repo = _make_repo("interview_q3")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "今年要開第二家店")
    # 最後一則包含 completed 訊息
    assert any("🎉" in r["text"] or "直接跟我說" in r["text"] for r in replies)
    repo.update_onboarding_step.assert_called_once_with("t1", "completed")


# ── Redo 功能（上一題不遺失已存資料）────────────────────────────────────────


@pytest.mark.asyncio
async def test_redo_from_q2_to_q1() -> None:
    repo = _make_repo("interview_q2")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "上一題")
    assert "重新來" in replies[0]["text"]
    assert "第 1 題" in replies[0]["text"]
    # 已刪除 core_value 的知識條目
    repo.delete_knowledge_entries_by_category.assert_called_once_with("t1", "core_value")
    repo.update_onboarding_step.assert_called_once_with("t1", "interview_q1")


@pytest.mark.asyncio
async def test_redo_from_q3_to_q2() -> None:
    repo = _make_repo("interview_q3")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "上一題")
    assert "第 2 題" in replies[0]["text"]
    repo.delete_knowledge_entries_by_category.assert_called_once_with("t1", "pain_point")
    repo.update_onboarding_step.assert_called_once_with("t1", "interview_q2")


@pytest.mark.asyncio
async def test_redo_from_q3_to_q1() -> None:
    """第一題重新回答，應刪除 core_value + pain_point。"""
    repo = _make_repo("interview_q3")
    flow = OnboardingFlow(repo)
    replies = await flow.handle_message("t1", "text", "第一題重新回答")
    assert "第 1 題" in replies[0]["text"]
    calls = {c.args[1] for c in repo.delete_knowledge_entries_by_category.call_args_list}
    assert "core_value" in calls
    assert "pain_point" in calls
