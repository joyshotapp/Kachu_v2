"""module 4 durable suggestions and recurring jobs

Revision ID: 20260509_0008
Revises: 20260509_0007
Create Date: 2026-05-09 07:20:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0008"
down_revision = "20260509_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kachu_suggestions", sa.Column("category", sa.Text, nullable=False, server_default=""))
    op.add_column("kachu_suggestions", sa.Column("reason", sa.Text, nullable=False, server_default=""))
    op.add_column("kachu_suggestions", sa.Column("affected_profile_ids_json", sa.Text, nullable=False, server_default="[]"))
    op.add_column("kachu_suggestions", sa.Column("profile_count", sa.Integer, nullable=False, server_default="0"))
    op.add_column("kachu_suggestions", sa.Column("suggested_action", sa.Text, nullable=False, server_default=""))
    op.add_column("kachu_suggestions", sa.Column("draft_message", sa.Text, nullable=False, server_default=""))
    op.add_column("kachu_suggestions", sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("kachu_suggestions", sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("kachu_suggestions", sa.Column("result_snapshot_json", sa.Text, nullable=False, server_default="{}"))
    op.execute("UPDATE kachu_suggestions SET reason = body WHERE reason = ''")
    op.create_index("ix_kachu_suggestions_category", "kachu_suggestions", ["category"])
    op.create_index("ix_kachu_suggestions_expires_at", "kachu_suggestions", ["expires_at"])
    op.create_index("ix_kachu_suggestions_sent_at", "kachu_suggestions", ["sent_at"])

    op.create_table(
        "kachu_recurring_jobs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("job_type", sa.Text, nullable=False, server_default=""),
        sa.Column("next_run_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("last_run_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("locked_until", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("last_result_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "job_type", name="uq_kachu_recurring_job_tenant_type"),
    )
    op.create_index("ix_kachu_recurring_jobs_tenant_id", "kachu_recurring_jobs", ["tenant_id"])
    op.create_index("ix_kachu_recurring_jobs_job_type", "kachu_recurring_jobs", ["job_type"])
    op.create_index("ix_kachu_recurring_jobs_next_run_at", "kachu_recurring_jobs", ["next_run_at"])
    op.create_index("ix_kachu_recurring_jobs_last_run_at", "kachu_recurring_jobs", ["last_run_at"])
    op.create_index("ix_kachu_recurring_jobs_locked_until", "kachu_recurring_jobs", ["locked_until"])


def downgrade() -> None:
    op.drop_index("ix_kachu_recurring_jobs_locked_until", table_name="kachu_recurring_jobs")
    op.drop_index("ix_kachu_recurring_jobs_last_run_at", table_name="kachu_recurring_jobs")
    op.drop_index("ix_kachu_recurring_jobs_next_run_at", table_name="kachu_recurring_jobs")
    op.drop_index("ix_kachu_recurring_jobs_job_type", table_name="kachu_recurring_jobs")
    op.drop_index("ix_kachu_recurring_jobs_tenant_id", table_name="kachu_recurring_jobs")
    op.drop_table("kachu_recurring_jobs")

    op.drop_index("ix_kachu_suggestions_sent_at", table_name="kachu_suggestions")
    op.drop_index("ix_kachu_suggestions_expires_at", table_name="kachu_suggestions")
    op.drop_index("ix_kachu_suggestions_category", table_name="kachu_suggestions")
    op.drop_column("kachu_suggestions", "result_snapshot_json")
    op.drop_column("kachu_suggestions", "sent_at")
    op.drop_column("kachu_suggestions", "expires_at")
    op.drop_column("kachu_suggestions", "draft_message")
    op.drop_column("kachu_suggestions", "suggested_action")
    op.drop_column("kachu_suggestions", "profile_count")
    op.drop_column("kachu_suggestions", "affected_profile_ids_json")
    op.drop_column("kachu_suggestions", "reason")
    op.drop_column("kachu_suggestions", "category")
