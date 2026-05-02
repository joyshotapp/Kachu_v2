"""WP-7: Edit session lifecycle to edited_payload approval."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from kachu.approval_bridge import ApprovalBridge
from kachu.config import Settings
from kachu.models import ApprovalAction
from kachu.persistence import KachuRepository, create_db_engine, init_db


@pytest.fixture()
def settings() -> Settings:
    return Settings(
        LINE_CHANNEL_ACCESS_TOKEN="",
        LINE_CHANNEL_SECRET="",
        LINE_BOSS_USER_ID="U_boss",
        AGENTOS_BASE_URL="http://agentos-mock",
        KACHU_BASE_URL="http://localhost:8001",
        DATABASE_URL="sqlite://",
    )


@pytest.fixture()
def repo(settings: Settings) -> KachuRepository:
    engine = create_db_engine(settings.DATABASE_URL)
    init_db(engine)
    return KachuRepository(engine)


@pytest.mark.asyncio
async def test_start_edit_session_creates_waiting_feedback_record(
    repo: KachuRepository, settings: Settings,
) -> None:
    repo.create_pending_approval(
        tenant_id="tenant-001",
        agentos_run_id="run-edit-001",
        workflow_type="photo_content",
        draft_content={
            "ig_fb": "原始 IG 草稿",
            "google": "原始 Google 草稿",
        },
    )
    bridge = ApprovalBridge(agentOS_client=AsyncMock(), repository=repo, settings=settings)

    await bridge.handle_postback(
        run_id="run-edit-001",
        tenant_id="tenant-001",
        action=ApprovalAction.EDIT,
        actor_line_id="U_boss",
    )

    active = repo.get_active_edit_session("tenant-001")
    assert active is not None
    assert active.run_id == "run-edit-001"
    assert active.step == "waiting_feedback"
    assert active.original_ig_draft == "原始 IG 草稿"
    assert active.original_google_draft == "原始 Google 草稿"


@pytest.mark.asyncio
async def test_complete_edit_session_submits_edited_payload(
    repo: KachuRepository, settings: Settings,
) -> None:
    repo.create_pending_approval(
        tenant_id="tenant-001",
        agentos_run_id="run-edit-002",
        workflow_type="photo_content",
        draft_content={
            "ig_fb": "原始 IG 草稿",
            "google": "原始 Google 草稿",
        },
    )
    mock_agentos = AsyncMock()
    mock_agentos.get_pending_approval_id_for_run.return_value = "approval-002"
    mock_agentos.decide_approval.return_value = MagicMock(
        run={"id": "run-edit-002", "status": "running"}
    )
    bridge = ApprovalBridge(agentOS_client=mock_agentos, repository=repo, settings=settings)

    await bridge.handle_postback(
        run_id="run-edit-002",
        tenant_id="tenant-001",
        action=ApprovalAction.EDIT,
        actor_line_id="U_boss",
    )
    edit_session = repo.get_active_edit_session("tenant-001")
    assert edit_session is not None

    repo.update_edit_session_draft(edit_session.id, "ig_fb", "修改後 IG 文案")
    repo.advance_edit_session(edit_session.id, "waiting_google")
    repo.update_edit_session_draft(edit_session.id, "google", "修改後 Google 文案")

    updated = repo.get_active_edit_session("tenant-001")
    assert updated is not None
    assert updated.step == "waiting_google"
    assert updated.edited_ig_draft == "修改後 IG 文案"
    assert updated.edited_google_draft == "修改後 Google 文案"

    repo.complete_edit_session(edit_session.id)
    assert repo.get_active_edit_session("tenant-001") is None

    submitted = await bridge.complete_edit_and_approve(
        run_id="run-edit-002",
        actor_line_id="U_boss",
        edited_ig_draft=updated.edited_ig_draft,
        edited_google_draft=updated.edited_google_draft,
    )

    assert submitted is True
    mock_agentos.get_pending_approval_id_for_run.assert_called_once_with("run-edit-002")
    mock_agentos.decide_approval.assert_called_once()
    decision = mock_agentos.decide_approval.call_args[0][1]
    assert decision.decision == "approved"
    assert decision.edited_payload == {
        "ig_fb": "修改後 IG 文案",
        "google": "修改後 Google 文案",
    }

    pending = repo.get_pending_approval_by_run_id("run-edit-002")
    assert pending is not None
    assert pending.status == "decided"
    assert pending.decision == "approved"
    assert pending.actor_line_id == "U_boss"


@pytest.mark.asyncio
async def test_complete_edit_session_failure_keeps_pending_approval_open(
    repo: KachuRepository, settings: Settings,
) -> None:
    repo.create_pending_approval(
        tenant_id="tenant-001",
        agentos_run_id="run-edit-003",
        workflow_type="photo_content",
        draft_content={
            "ig_fb": "原始 IG 草稿",
            "google": "原始 Google 草稿",
        },
    )
    mock_agentos = AsyncMock()
    mock_agentos.get_pending_approval_id_for_run.return_value = "approval-003"
    mock_agentos.decide_approval.side_effect = httpx.ReadTimeout("agentos unavailable")
    bridge = ApprovalBridge(agentOS_client=mock_agentos, repository=repo, settings=settings)

    submitted = await bridge.complete_edit_and_approve(
        run_id="run-edit-003",
        actor_line_id="U_boss",
        edited_ig_draft="修改後 IG 文案",
        edited_google_draft="修改後 Google 文案",
    )

    assert submitted is False
    pending = repo.get_pending_approval_by_run_id("run-edit-003")
    assert pending is not None
    assert pending.status == "pending"
    assert pending.decision is None


@pytest.mark.asyncio
async def test_complete_edit_session_unexpected_error_bubbles_up(
    repo: KachuRepository, settings: Settings,
) -> None:
    repo.create_pending_approval(
        tenant_id="tenant-001",
        agentos_run_id="run-edit-004",
        workflow_type="photo_content",
        draft_content={
            "ig_fb": "原始 IG 草稿",
            "google": "原始 Google 草稿",
        },
    )
    mock_agentos = AsyncMock()
    mock_agentos.get_pending_approval_id_for_run.return_value = "approval-004"
    mock_agentos.decide_approval.side_effect = AssertionError("unexpected agentos bug")
    bridge = ApprovalBridge(agentOS_client=mock_agentos, repository=repo, settings=settings)

    with pytest.raises(AssertionError, match="unexpected agentos bug"):
        await bridge.complete_edit_and_approve(
            run_id="run-edit-004",
            actor_line_id="U_boss",
            edited_ig_draft="修改後 IG 文案",
            edited_google_draft="修改後 Google 文案",
        )

