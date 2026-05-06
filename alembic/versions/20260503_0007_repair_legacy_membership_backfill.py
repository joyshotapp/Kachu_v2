"""repair legacy membership backfill

Revision ID: 20260503_0007
Revises: 20260503_0006
Create Date: 2026-05-03 12:20:00
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import sqlalchemy as sa
from alembic import op


revision = "20260503_0007"
down_revision = "20260503_0006"
branch_labels = None
depends_on = None


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
    rows = connection.execute(
        sa.text(
            """
            SELECT id, line_user_id, name
            FROM kachu_tenants
            WHERE id IS NOT NULL AND TRIM(id) <> ''
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
    connection.execute(
        sa.text(
            """
            DELETE FROM kachu_tenant_memberships
            WHERE role = 'owner'
              AND tenant_id IN (
                SELECT id FROM kachu_tenants WHERE line_user_id IS NULL OR TRIM(line_user_id) = ''
              )
              AND tenant_id = line_user_id
            """
        )
    )