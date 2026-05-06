"""add tenant merchant slug

Revision ID: 20260505_0009
Revises: 20260503_0008
Create Date: 2026-05-05 23:10:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260505_0009"
down_revision = "20260503_0008"
branch_labels = None
depends_on = None


def _column_names(connection: sa.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    connection = op.get_bind()
    existing_columns = _column_names(connection, "kachu_tenants")
    if "merchant_slug" not in existing_columns:
        op.add_column(
            "kachu_tenants",
            sa.Column("merchant_slug", sa.String(), nullable=False, server_default=""),
        )


def downgrade() -> None:
    connection = op.get_bind()
    existing_columns = _column_names(connection, "kachu_tenants")
    if "merchant_slug" in existing_columns:
        op.drop_column("kachu_tenants", "merchant_slug")