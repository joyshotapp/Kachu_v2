"""
Tests for Phase 4: Adaptive Approval Policy
- KachuExecutionPolicyResolver
- TenantApprovalProfileTable compute logic
"""
from __future__ import annotations

import pytest
import httpx
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy.exc import SQLAlchemyError

from kachu.policy import KachuExecutionPolicyResolver, PolicyHints, _HIGH_TRUST_TIMEOUT, _DEFAULT_TIMEOUT
from kachu.scheduler import KachuScheduler
from kachu.persistence.tables import TenantApprovalProfileTable


def _utcnow():
    return datetime.now(timezone.utc)


# ── PolicyHints.to_workflow_input_patch ──────────────────────────────────────

def test_policy_hints_default_patch():
    hints = PolicyHints()
    patch = hints.to_workflow_input_patch()
    assert patch["approval_timeout_seconds"] == _DEFAULT_TIMEOUT
    assert patch["require_direction_check"] is False
    assert patch["policy_generation_context"] == ""


def test_policy_hints_high_trust_patch():
    hints = PolicyHints(approval_timeout_seconds=_HIGH_TRUST_TIMEOUT)
    patch = hints.to_workflow_input_patch()
    assert patch["approval_timeout_seconds"] == _HIGH_TRUST_TIMEOUT


# ── KachuExecutionPolicyResolver ─────────────────────────────────────────────

def _make_resolver(profile):
    repo = MagicMock()
    repo.get_approval_profile.return_value = profile
    return KachuExecutionPolicyResolver(repo)


def test_resolver_no_profile_returns_defaults():
    resolver = _make_resolver(None)
    hints = resolver.resolve("tenant-A")
    assert hints.approval_timeout_seconds == _DEFAULT_TIMEOUT
    assert hints.require_direction_check is False
    assert hints.source == "insufficient_data"


def test_resolver_insufficient_data():
    profile = TenantApprovalProfileTable(
        tenant_id="tenant-A",
        recent_acceptance_rate=0.9,
        median_edit_delta=0.05,
        avg_approval_latency_seconds=3600,
        total_decisions=2,   # < 3
        updated_at=_utcnow(),
    )
    resolver = _make_resolver(profile)
    hints = resolver.resolve("tenant-A")
    assert hints.source == "insufficient_data"


def test_resolver_high_trust():
    profile = TenantApprovalProfileTable(
        tenant_id="tenant-A",
        recent_acceptance_rate=0.90,
        median_edit_delta=0.05,
        avg_approval_latency_seconds=3600,
        total_decisions=10,
        updated_at=_utcnow(),
    )
    resolver = _make_resolver(profile)
    hints = resolver.resolve("tenant-A")
    assert hints.approval_timeout_seconds == _HIGH_TRUST_TIMEOUT
    assert hints.require_direction_check is False
    assert hints.source == "high_trust"


def test_resolver_low_trust():
    profile = TenantApprovalProfileTable(
        tenant_id="tenant-A",
        recent_acceptance_rate=0.40,
        median_edit_delta=0.5,
        avg_approval_latency_seconds=86400,
        total_decisions=8,
        updated_at=_utcnow(),
    )
    resolver = _make_resolver(profile)
    hints = resolver.resolve("tenant-A")
    assert hints.require_direction_check is True
    assert hints.approval_timeout_seconds == _DEFAULT_TIMEOUT
    assert hints.source == "low_trust"


def test_resolver_normal_trust():
    profile = TenantApprovalProfileTable(
        tenant_id="tenant-A",
        recent_acceptance_rate=0.70,
        median_edit_delta=0.20,
        avg_approval_latency_seconds=43200,
        total_decisions=5,
        updated_at=_utcnow(),
    )
    resolver = _make_resolver(profile)
    hints = resolver.resolve("tenant-A")
    assert hints.source == "normal"
    assert hints.approval_timeout_seconds == _DEFAULT_TIMEOUT
    assert hints.require_direction_check is False


def test_resolver_repo_error_returns_fallback():
    repo = MagicMock()
    repo.get_approval_profile.side_effect = SQLAlchemyError("DB error")
    resolver = KachuExecutionPolicyResolver(repo)
    hints = resolver.resolve("tenant-A")
    assert hints.source == "error_fallback"
    assert hints.approval_timeout_seconds == _DEFAULT_TIMEOUT


def test_resolver_re_raises_unexpected_error():
    repo = MagicMock()
    repo.get_approval_profile.side_effect = AssertionError("unexpected")
    resolver = KachuExecutionPolicyResolver(repo)

    with pytest.raises(AssertionError, match="unexpected"):
        resolver.resolve("tenant-A")


@pytest.mark.asyncio
async def test_scheduler_google_posts_includes_policy_hints():
    repo = MagicMock()
    repo.list_active_tenant_ids.return_value = ["tenant-A"]
    settings = MagicMock()
    agentOS = MagicMock()
    agentOS.create_task = pytest.importorskip("unittest.mock").AsyncMock(return_value=MagicMock(task={"id": "task-1"}))
    agentOS.run_task = pytest.importorskip("unittest.mock").AsyncMock()
    policy_resolver = MagicMock()
    policy_resolver.resolve.return_value = PolicyHints(
        approval_timeout_seconds=21600,
        require_direction_check=True,
        generation_context="請先收斂主題",
        source="test",
    )

    scheduler = KachuScheduler(agentOS, repo, settings, policy_resolver=policy_resolver)
    await scheduler._trigger_google_posts()

    task_request = agentOS.create_task.call_args[0][0]
    assert task_request.workflow_input["approval_timeout_seconds"] == 21600
    assert task_request.workflow_input["require_direction_check"] is True
    assert task_request.workflow_input["policy_generation_context"] == "請先收斂主題"


@pytest.mark.asyncio
async def test_scheduler_google_posts_logs_recoverable_agentos_error():
    repo = MagicMock()
    repo.list_active_tenant_ids.return_value = ["tenant-A"]
    settings = MagicMock()
    agentOS = MagicMock()
    agentOS.create_task = AsyncMock(side_effect=httpx.ReadTimeout("timeout"))
    agentOS.run_task = AsyncMock()

    scheduler = KachuScheduler(agentOS, repo, settings)
    await scheduler._trigger_google_posts()

    agentOS.run_task.assert_not_called()


@pytest.mark.asyncio
async def test_scheduler_google_posts_re_raises_unexpected_error():
    repo = MagicMock()
    repo.list_active_tenant_ids.return_value = ["tenant-A"]
    settings = MagicMock()
    agentOS = MagicMock()
    agentOS.create_task = AsyncMock(side_effect=AssertionError("unexpected"))
    agentOS.run_task = AsyncMock()

    scheduler = KachuScheduler(agentOS, repo, settings)
    with pytest.raises(AssertionError, match="unexpected"):
        await scheduler._trigger_google_posts()
