from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from kachu.post_task_review import PostTaskReviewService


@pytest.mark.asyncio
async def test_after_approval_decision_updates_episode_profile_and_briefs() -> None:
    repo = MagicMock()
    memory = MagicMock()
    context_brief_manager = MagicMock()
    context_brief_manager.refresh_briefs = AsyncMock()
    service = PostTaskReviewService(repo, memory, context_brief_manager)

    await service.after_approval_decision(
        tenant_id="tenant-1",
        workflow_type="photo_content",
        outcome="approved",
        context_summary={"run_id": "run-1"},
        refresh_reason="approval_decision",
    )

    memory.record_episode.assert_called_once_with(
        tenant_id="tenant-1",
        workflow_type="photo_content",
        outcome="approved",
        context_summary={"run_id": "run-1"},
    )
    repo.compute_and_save_approval_profile.assert_called_once_with("tenant-1")
    context_brief_manager.refresh_briefs.assert_awaited_once_with(
        "tenant-1",
        reason="approval_decision",
    )


@pytest.mark.asyncio
async def test_after_preference_update_can_skip_brief_refresh() -> None:
    repo = MagicMock()
    memory = MagicMock()
    context_brief_manager = MagicMock()
    context_brief_manager.refresh_briefs = AsyncMock()
    service = PostTaskReviewService(repo, memory, context_brief_manager)

    await service.after_preference_update(
        tenant_id="tenant-1",
        platform="google",
        original_draft="old",
        edited_draft="new",
        run_id="run-2",
        refresh_briefs=False,
    )

    memory.store_preference.assert_called_once_with(
        tenant_id="tenant-1",
        platform="google",
        original_draft="old",
        edited_draft="new",
        run_id="run-2",
    )
    context_brief_manager.refresh_briefs.assert_not_called()