from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from kachu.approval_bridge import ApprovalBridge
from kachu.config import Settings
from kachu.main import create_app
from kachu.models import ApprovalAction


@pytest.fixture
def client() -> TestClient:
    app = create_app(
        Settings(
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_phase6",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="http://localhost:8001",
            DATABASE_URL="sqlite://",
        )
    )
    return TestClient(app)


def test_dashboard_audit_returns_notify_and_publish_events(client: TestClient) -> None:
    run_id = "run-audit-001"
    notify = client.post(
        "/tools/notify-approval",
        json={
            "tenant_id": "tenant-audit",
            "run_id": run_id,
            "workflow": "kachu_photo_content",
            "drafts": {"ig_fb": "draft", "google": "draft"},
        },
    )
    assert notify.status_code == 200

    publish = client.post(
        "/tools/publish-content",
        json={
            "tenant_id": "tenant-audit",
            "run_id": run_id,
            "selected_platforms": ["google", "ig_fb"],
            "drafts": {"ig_fb": "draft", "google": "draft"},
        },
    )
    assert publish.status_code == 200

    audit = client.get(f"/dashboard/api/audit?tenant_id=tenant-audit&run_id={run_id}")
    assert audit.status_code == 200
    event_types = [event["event_type"] for event in audit.json()["events"]]
    assert "approval_requested" in event_types
    assert "push_skipped" in event_types
    assert "publish_attempted" in event_types
    assert any(event_type in event_types for event_type in ["publish_succeeded", "publish_skipped", "publish_failed"])


def test_dashboard_audit_supports_event_type_filter(client: TestClient) -> None:
    run_id = "run-audit-filter-001"
    client.post(
        "/tools/notify-approval",
        json={
            "tenant_id": "tenant-audit",
            "run_id": run_id,
            "workflow": "kachu_photo_content",
            "drafts": {"ig_fb": "draft", "google": "draft"},
        },
    )

    response = client.get(
        f"/dashboard/api/audit?tenant_id=tenant-audit&run_id={run_id}&event_type=approval_requested"
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] >= 1
    assert all(event["event_type"] == "approval_requested" for event in payload["events"])


@pytest.mark.asyncio
async def test_approval_bridge_records_approval_decision_audit() -> None:
    app = create_app(
        Settings(
            LINE_CHANNEL_ACCESS_TOKEN="",
            LINE_CHANNEL_SECRET="",
            LINE_BOSS_USER_ID="U_boss_phase6",
            AGENTOS_BASE_URL="http://agentos-mock",
            KACHU_BASE_URL="http://localhost:8001",
            DATABASE_URL="sqlite://",
        )
    )
    repo = app.state.repository
    repo.create_pending_approval(
        tenant_id="tenant-audit",
        agentos_run_id="run-audit-approve",
        workflow_type="kachu_photo_content",
        draft_content={"ig_fb": "draft"},
    )

    agentos = AsyncMock()
    agentos.get_pending_approval_id_for_run.return_value = "approval-001"
    agentos.decide_approval.return_value = MagicMock(run={"status": "completed"})

    bridge = ApprovalBridge(
        agentOS_client=agentos,
        repository=repo,
        settings=MagicMock(LINE_CHANNEL_ACCESS_TOKEN=""),
    )
    await bridge.handle_postback(
        run_id="run-audit-approve",
        tenant_id="tenant-audit",
        action=ApprovalAction.APPROVE,
        actor_line_id="U_boss_phase6",
    )

    audit_events = repo.list_audit_events(
        tenant_id="tenant-audit",
        agentos_run_id="run-audit-approve",
    )
    assert any(event.event_type == "approval_decided" for event in audit_events)