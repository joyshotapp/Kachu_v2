from __future__ import annotations

from types import SimpleNamespace

from kachu.line.push import resolve_tenant_line_recipients


LEGACY_FALLBACK_BOSS_USER_ID = "U-legacy-boss"


def test_resolve_tenant_line_recipients_prefers_notification_memberships() -> None:
    repo = SimpleNamespace(get_notification_line_user_ids=lambda tenant_id: ["U-owner-1", "U-manager-1"])
    settings = SimpleNamespace(LINE_BOSS_USER_ID=LEGACY_FALLBACK_BOSS_USER_ID)

    recipients = resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id="tenant-a")

    assert recipients == ["U-owner-1", "U-manager-1"]


def test_resolve_tenant_line_recipients_prefers_membership_owners() -> None:
    repo = SimpleNamespace(get_owner_line_user_ids=lambda tenant_id: ["U-owner-1", "U-owner-2"])
    settings = SimpleNamespace(LINE_BOSS_USER_ID=LEGACY_FALLBACK_BOSS_USER_ID)

    recipients = resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id="tenant-a")

    assert recipients == ["U-owner-1", "U-owner-2"]


def test_resolve_tenant_line_recipients_falls_back_to_legacy_boss() -> None:
    repo = SimpleNamespace()
    settings = SimpleNamespace(LINE_BOSS_USER_ID=LEGACY_FALLBACK_BOSS_USER_ID)

    recipients = resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id="tenant-a")

    assert recipients == ["U-legacy-boss"]


def test_resolve_tenant_line_recipients_returns_empty_when_concrete_lookup_has_no_owner() -> None:
    repo = SimpleNamespace(get_owner_line_user_ids=lambda tenant_id: [])
    settings = SimpleNamespace(LINE_BOSS_USER_ID=LEGACY_FALLBACK_BOSS_USER_ID)

    recipients = resolve_tenant_line_recipients(repo=repo, settings=settings, tenant_id="tenant-a")

    assert recipients == []