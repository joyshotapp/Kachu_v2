"""module 2/4/5 approval suggestions learning

Revision ID: 20260509_0006
Revises: 20260509_0005
Create Date: 2026-05-09 05:10:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0006"
down_revision = "20260509_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_pending_approvals",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("agentos_task_id", sa.Text, nullable=False, server_default=""),
        sa.Column("agentos_run_id", sa.Text, nullable=False),
        sa.Column("workflow_type", sa.Text, nullable=False, server_default=""),
        sa.Column("review_id", sa.Text, nullable=False, server_default=""),
        sa.Column("draft_content", sa.Text, nullable=False, server_default="{}"),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("actor_line_id", sa.Text, nullable=False, server_default=""),
        sa.Column("decision_payload_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.UniqueConstraint("agentos_run_id", name="uq_pending_approval_run"),
    )
    op.create_index("ix_pending_approval_run", "kachu_pending_approvals", ["agentos_run_id"])
    op.create_index("ix_pending_approval_status", "kachu_pending_approvals", ["status"])

    op.create_table(
        "kachu_published_content_records",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("workflow_type", sa.Text, nullable=False, server_default=""),
        sa.Column("channel", sa.Text, nullable=False, server_default=""),
        sa.Column("source_id", sa.Text, nullable=False, server_default=""),
        sa.Column("source_ref", sa.Text, nullable=False, server_default=""),
        sa.Column("content_text", sa.Text, nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_published_tenant", "kachu_published_content_records", ["tenant_id"])

    op.create_table(
        "kachu_suggestions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("suggestion_type", sa.Text, nullable=False, server_default=""),
        sa.Column("title", sa.Text, nullable=False, server_default=""),
        sa.Column("body", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, nullable=False, server_default="pending"),
        sa.Column("related_run_id", sa.Text, nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "kachu_preference_memories",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("platform", sa.Text, nullable=False, server_default=""),
        sa.Column("original_draft", sa.Text, nullable=False, server_default=""),
        sa.Column("edited_draft", sa.Text, nullable=False, server_default=""),
        sa.Column("diff_notes", sa.Text, nullable=False, server_default=""),
        sa.Column("run_id", sa.Text, nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "kachu_context_briefs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("brief_type", sa.Text, nullable=False, server_default=""),
        sa.Column("content_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "kachu_episodic_memories",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("workflow_type", sa.Text, nullable=False, server_default=""),
        sa.Column("outcome", sa.Text, nullable=False, server_default=""),
        sa.Column("context_summary_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "kachu_approval_profiles",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("total_decisions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("recent_acceptance_rate", sa.Float, nullable=False, server_default="0"),
        sa.Column("median_edit_delta", sa.Float, nullable=False, server_default="0"),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", name="uq_approval_profile_tenant"),
    )


def downgrade() -> None:
    op.drop_table("kachu_approval_profiles")
    op.drop_table("kachu_episodic_memories")
    op.drop_table("kachu_context_briefs")
    op.drop_table("kachu_preference_memories")
    op.drop_table("kachu_suggestions")
    op.drop_index("ix_published_tenant", table_name="kachu_published_content_records")
    op.drop_table("kachu_published_content_records")
    op.drop_index("ix_pending_approval_status", table_name="kachu_pending_approvals")
    op.drop_index("ix_pending_approval_run", table_name="kachu_pending_approvals")
    op.drop_table("kachu_pending_approvals")