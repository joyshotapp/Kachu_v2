"""add knowledge lifecycle metadata

Revision ID: 20260503_0008
Revises: 20260503_0007
Create Date: 2026-05-03 21:10:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


revision = "20260503_0008"
down_revision = "20260503_0007"
branch_labels = None
depends_on = None


def _column_names(connection: sa.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    connection = op.get_bind()
    existing_columns = _column_names(connection, "kachu_knowledge_entries")
    if "last_retrieved_at" not in existing_columns:
        op.add_column(
            "kachu_knowledge_entries",
            sa.Column("last_retrieved_at", sa.DateTime(timezone=True), nullable=True),
        )
    if "last_reviewed_at" not in existing_columns:
        op.add_column(
            "kachu_knowledge_entries",
            sa.Column("last_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        )


def downgrade() -> None:
    connection = op.get_bind()
    existing_columns = _column_names(connection, "kachu_knowledge_entries")
    if "last_reviewed_at" in existing_columns:
        op.drop_column("kachu_knowledge_entries", "last_reviewed_at")
    if "last_retrieved_at" in existing_columns:
        op.drop_column("kachu_knowledge_entries", "last_retrieved_at")