from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from kachu.intent_router import IntentRouter
from kachu.models import Intent
from kachu.policy import PolicyHints


@pytest.mark.asyncio
async def test_google_post_dispatch_keeps_policy_hints_on_boss_request() -> None:
    agentos = AsyncMock()
    agentos.create_task.return_value = SimpleNamespace(task={"id": "task-1"})
    agentos.run_task.return_value = SimpleNamespace(run={"id": "run-1", "status": "waiting_approval"})

    repo = MagicMock()
    resolver = MagicMock()
    resolver.resolve.return_value = PolicyHints(
        approval_timeout_seconds=21600,
        require_direction_check=True,
        generation_context="請先收斂主題",
        source="test",
    )

    router = IntentRouter(agentOS_client=agentos, repository=repo, policy_resolver=resolver)
    await router.dispatch(
        intent=Intent.GOOGLE_POST,
        tenant_id="tenant-phase6",
        trigger_source="line",
        trigger_payload={"message": "幫我寫本週 Google 商家動態"},
    )

    task_request = agentos.create_task.call_args[0][0]
    assert task_request.domain == "kachu_google_post"
    assert task_request.workflow_input["trigger_source"] == "boss_request"
    assert task_request.workflow_input["approval_timeout_seconds"] == 21600
    assert task_request.workflow_input["require_direction_check"] is True
    assert task_request.workflow_input["policy_generation_context"] == "請先收斂主題"


@pytest.mark.asyncio
async def test_photo_content_dispatch_merges_policy_hints_and_calendar_hint() -> None:
    agentos = AsyncMock()
    agentos.create_task.return_value = SimpleNamespace(task={"id": "task-1"})
    agentos.run_task.return_value = SimpleNamespace(run={"id": "run-1", "status": "waiting_approval"})

    repo = MagicMock()
    repo.get_shared_context.return_value = {
        "weeks": [{"topic": "春季新品"}],
    }
    resolver = MagicMock()
    resolver.resolve.return_value = PolicyHints(
        approval_timeout_seconds=86400,
        require_direction_check=False,
        generation_context="請維持品牌一致性",
        source="test",
    )

    router = IntentRouter(agentOS_client=agentos, repository=repo, policy_resolver=resolver)
    await router.dispatch(
        intent=Intent.PHOTO_CONTENT,
        tenant_id="tenant-phase6",
        trigger_source="line",
        trigger_payload={"line_message_id": "msg-1", "photo_url": "https://example.com/photo.jpg"},
    )

    task_request = agentos.create_task.call_args[0][0]
    assert task_request.domain == "kachu_photo_content"
    assert task_request.workflow_input["approval_timeout_seconds"] == 86400
    assert task_request.workflow_input["policy_generation_context"] == "請維持品牌一致性"
    assert task_request.workflow_input["calendar_topic_hint"] == "春季新品"