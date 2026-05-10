"""execute task tracking

Revision ID: 20260510_0011
Revises: 20260509_0010
Create Date: 2026-05-10 10:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_0011"
down_revision = "20260509_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_execute_task_records",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("intent_label", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("task_id", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="created"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("task_id", name="uq_kachu_execute_task_records_task_id"),
    )
    op.create_index("ix_kachu_execute_task_records_tenant_id", "kachu_execute_task_records", ["tenant_id"])
    op.create_index("ix_kachu_execute_task_records_line_user_id", "kachu_execute_task_records", ["line_user_id"])
    op.create_index("ix_kachu_execute_task_records_intent_label", "kachu_execute_task_records", ["intent_label"])
    op.create_index("ix_kachu_execute_task_records_task_id", "kachu_execute_task_records", ["task_id"])
    op.create_index("ix_kachu_execute_task_records_run_id", "kachu_execute_task_records", ["run_id"])
    op.create_index("ix_kachu_execute_task_records_status", "kachu_execute_task_records", ["status"])
    op.create_index("ix_kachu_execute_task_records_created_at", "kachu_execute_task_records", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_kachu_execute_task_records_created_at", table_name="kachu_execute_task_records")
    op.drop_index("ix_kachu_execute_task_records_status", table_name="kachu_execute_task_records")
    op.drop_index("ix_kachu_execute_task_records_run_id", table_name="kachu_execute_task_records")
    op.drop_index("ix_kachu_execute_task_records_task_id", table_name="kachu_execute_task_records")
    op.drop_index("ix_kachu_execute_task_records_intent_label", table_name="kachu_execute_task_records")
    op.drop_index("ix_kachu_execute_task_records_line_user_id", table_name="kachu_execute_task_records")
    op.drop_index("ix_kachu_execute_task_records_tenant_id", table_name="kachu_execute_task_records")
    op.drop_table("kachu_execute_task_records")
