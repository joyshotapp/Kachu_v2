"""add tenant feature flags

Revision ID: 20260503_0006
Revises: 20260503_0005
Create Date: 2026-05-03 21:35:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260503_0006"
down_revision = "20260503_0005"
branch_labels = None
depends_on = None


def _table_exists(connection: sa.Connection, table_name: str) -> bool:
    inspector = sa.inspect(connection)
    return table_name in inspector.get_table_names()


def upgrade() -> None:
    connection = op.get_bind()
    if _table_exists(connection, "kachu_tenant_feature_flags"):
        return

    op.create_table(
        "kachu_tenant_feature_flags",
        sa.Column("tenant_id", sa.String(), nullable=False),
        sa.Column("ga4_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("meta_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("cross_channel_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("crm_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("tenant_id"),
    )


def downgrade() -> None:
    connection = op.get_bind()
    if _table_exists(connection, "kachu_tenant_feature_flags"):
        op.drop_table("kachu_tenant_feature_flags")