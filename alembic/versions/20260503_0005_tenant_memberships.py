"""add tenant memberships

Revision ID: 20260503_0005
Revises: 20260502_0004
Create Date: 2026-05-03 12:00:00
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260503_0005"
down_revision = "20260502_0004"
branch_labels = None
depends_on = None


def _table_exists(connection: sa.Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def _index_names(connection: sa.Connection, table_name: str) -> set[str]:
    if not _table_exists(connection, table_name):
        return set()
    inspector = sa.inspect(connection)
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _resolve_legacy_line_user_id(row: sa.RowMapping) -> str:
    direct_line_user_id = str(row["line_user_id"] or "").strip()
    if direct_line_user_id:
        return direct_line_user_id

    legacy_tenant_id = str(row["id"] or "").strip()
    if legacy_tenant_id.startswith("U"):
        return legacy_tenant_id
    return ""


def upgrade() -> None:
    connection = op.get_bind()
    if not _table_exists(connection, "kachu_tenant_memberships"):
        op.create_table(
            "kachu_tenant_memberships",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("line_user_id", sa.String(), nullable=False),
            sa.Column("role", sa.String(), nullable=False, server_default="owner"),
            sa.Column("display_name", sa.String(), nullable=False, server_default=""),
            sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = _index_names(connection, "kachu_tenant_memberships")
    if "ix_kachu_tenant_memberships_tenant_id" not in existing_indexes:
        op.create_index(
            "ix_kachu_tenant_memberships_tenant_id",
            "kachu_tenant_memberships",
            ["tenant_id"],
        )
    if "ix_kachu_tenant_memberships_line_user_id" not in existing_indexes:
        op.create_index(
            "ix_kachu_tenant_memberships_line_user_id",
            "kachu_tenant_memberships",
            ["line_user_id"],
        )
    if "ix_kachu_tenant_memberships_tenant_active" not in existing_indexes:
        op.create_index(
            "ix_kachu_tenant_memberships_tenant_active",
            "kachu_tenant_memberships",
            ["tenant_id", "is_active"],
        )

    rows = connection.execute(
        sa.text(
            """
            SELECT id, line_user_id, name
            FROM kachu_tenants
            WHERE line_user_id IS NOT NULL AND TRIM(line_user_id) <> ''
            """
        )
    ).mappings()
    now = datetime.now(timezone.utc)
    for row in rows:
        normalized_line_user_id = _resolve_legacy_line_user_id(row)
        if not normalized_line_user_id:
            continue

        existing = connection.execute(
            sa.text(
                """
                SELECT 1
                FROM kachu_tenant_memberships
                WHERE tenant_id = :tenant_id AND line_user_id = :line_user_id
                LIMIT 1
                """
            ),
            {
                "tenant_id": row["id"],
                "line_user_id": normalized_line_user_id,
            },
        ).first()
        if existing is not None:
            continue

        connection.execute(
            sa.text(
                """
                INSERT INTO kachu_tenant_memberships (
                    id, tenant_id, line_user_id, role, display_name, is_active, created_at, updated_at
                ) VALUES (
                    :id, :tenant_id, :line_user_id, :role, :display_name, :is_active, :created_at, :updated_at
                )
                """
            ),
            {
                "id": str(uuid4()),
                "tenant_id": row["id"],
                "line_user_id": normalized_line_user_id,
                "role": "owner",
                "display_name": str(row["name"] or "").strip(),
                "is_active": True,
                "created_at": now,
                "updated_at": now,
            },
        )


def downgrade() -> None:
    connection = op.get_bind()
    if not _table_exists(connection, "kachu_tenant_memberships"):
        return

    existing_indexes = _index_names(connection, "kachu_tenant_memberships")
    if "ix_kachu_tenant_memberships_tenant_active" in existing_indexes:
        op.drop_index("ix_kachu_tenant_memberships_tenant_active", table_name="kachu_tenant_memberships")
    if "ix_kachu_tenant_memberships_line_user_id" in existing_indexes:
        op.drop_index("ix_kachu_tenant_memberships_line_user_id", table_name="kachu_tenant_memberships")
    if "ix_kachu_tenant_memberships_tenant_id" in existing_indexes:
        op.drop_index("ix_kachu_tenant_memberships_tenant_id", table_name="kachu_tenant_memberships")
    op.drop_table("kachu_tenant_memberships")