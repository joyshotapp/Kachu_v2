from __future__ import annotations

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from kachu.persistence.repository import KachuRepository


def _make_repo() -> KachuRepository:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    return KachuRepository(engine)


def test_create_and_lookup_active_tenant_membership() -> None:
    repo = _make_repo()

    membership = repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-owner-a",
        role="owner",
        display_name="Boss A",
    )

    assert membership.tenant_id == "tenant-a"
    assert membership.line_user_id == "U-owner-a"
    assert membership.role == "owner"

    resolved = repo.get_active_membership_by_line_user_id("U-owner-a")
    assert resolved is not None
    assert resolved.id == membership.id
    assert resolved.tenant_id == "tenant-a"


def test_create_tenant_membership_reuses_existing_row_for_same_binding() -> None:
    repo = _make_repo()

    first = repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-manager-a",
        role="manager",
        display_name="First Name",
    )
    second = repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-manager-a",
        role="owner",
        display_name="Updated Name",
    )

    assert second.id == first.id
    assert second.role == "owner"
    assert second.display_name == "Updated Name"
    assert len(repo.list_active_memberships("tenant-a")) == 1


def test_create_tenant_membership_rejects_cross_tenant_active_conflict() -> None:
    repo = _make_repo()

    repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-shared",
        role="owner",
    )

    with pytest.raises(ValueError, match="already bound"):
        repo.create_tenant_membership(
            tenant_id="tenant-b",
            line_user_id="U-shared",
            role="owner",
        )


def test_list_active_memberships_and_notification_recipients_ignore_inactive_rows() -> None:
    repo = _make_repo()

    owner = repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-owner-a",
        role="owner",
    )
    manager = repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-manager-a",
        role="manager",
    )

    repo.deactivate_tenant_membership(manager.id)

    active_memberships = repo.list_active_memberships("tenant-a")
    assert [item.id for item in active_memberships] == [owner.id]
    assert repo.get_owner_line_user_ids("tenant-a") == ["U-owner-a"]
    assert repo.get_notification_line_user_ids("tenant-a") == ["U-owner-a"]


def test_notification_recipients_include_active_owner_and_manager() -> None:
    repo = _make_repo()

    repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-owner-a",
        role="owner",
    )
    repo.create_tenant_membership(
        tenant_id="tenant-a",
        line_user_id="U-manager-a",
        role="manager",
    )

    assert repo.get_notification_line_user_ids("tenant-a") == ["U-owner-a", "U-manager-a"]