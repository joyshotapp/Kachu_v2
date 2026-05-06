"""add line scheduled publishes

Revision ID: 20260502_0004
Revises: 20260502_0003
Create Date: 2026-05-02 22:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260502_0004"
down_revision = "20260502_0003"
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


def upgrade() -> None:
    connection = op.get_bind()
    if not _table_exists(connection, "kachu_scheduled_publishes"):
        op.create_table(
            "kachu_scheduled_publishes",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("tenant_id", sa.String(), nullable=False),
            sa.Column("source_run_id", sa.String(), nullable=False, server_default=""),
            sa.Column("workflow_type", sa.String(), nullable=False, server_default=""),
            sa.Column("selected_platforms", sa.String(), nullable=False, server_default="[]"),
            sa.Column("draft_content", sa.String(), nullable=False, server_default="{}"),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("actor_line_id", sa.String(), nullable=False, server_default=""),
            sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=False),
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error_message", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    existing_indexes = _index_names(connection, "kachu_scheduled_publishes")
    if "ix_kachu_scheduled_publishes_tenant_id" not in existing_indexes:
        op.create_index(
            "ix_kachu_scheduled_publishes_tenant_id",
            "kachu_scheduled_publishes",
            ["tenant_id"],
        )
    if "ix_kachu_scheduled_publishes_source_run_id" not in existing_indexes:
        op.create_index(
            "ix_kachu_scheduled_publishes_source_run_id",
            "kachu_scheduled_publishes",
            ["source_run_id"],
        )
    if "ix_kachu_scheduled_publishes_status" not in existing_indexes:
        op.create_index(
            "ix_kachu_scheduled_publishes_status",
            "kachu_scheduled_publishes",
            ["status"],
        )
    if "ix_kachu_scheduled_publishes_scheduled_for" not in existing_indexes:
        op.create_index(
            "ix_kachu_scheduled_publishes_scheduled_for",
            "kachu_scheduled_publishes",
            ["scheduled_for"],
        )


def downgrade() -> None:
    connection = op.get_bind()
    if not _table_exists(connection, "kachu_scheduled_publishes"):
        return

    existing_indexes = _index_names(connection, "kachu_scheduled_publishes")
    if "ix_kachu_scheduled_publishes_scheduled_for" in existing_indexes:
        op.drop_index("ix_kachu_scheduled_publishes_scheduled_for", table_name="kachu_scheduled_publishes")
    if "ix_kachu_scheduled_publishes_status" in existing_indexes:
        op.drop_index("ix_kachu_scheduled_publishes_status", table_name="kachu_scheduled_publishes")
    if "ix_kachu_scheduled_publishes_source_run_id" in existing_indexes:
        op.drop_index("ix_kachu_scheduled_publishes_source_run_id", table_name="kachu_scheduled_publishes")
    if "ix_kachu_scheduled_publishes_tenant_id" in existing_indexes:
        op.drop_index("ix_kachu_scheduled_publishes_tenant_id", table_name="kachu_scheduled_publishes")
    op.drop_table("kachu_scheduled_publishes")