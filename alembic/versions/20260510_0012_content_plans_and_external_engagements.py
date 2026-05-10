"""add content plans and external engagements

Revision ID: 20260510_0012
Revises: 20260510_0011
Create Date: 2026-05-10 19:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_0012"
down_revision = "20260510_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_content_plans",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_conversation_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_by_line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("objective", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="active"),
        sa.Column("plan_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kachu_content_plans_tenant_id", "kachu_content_plans", ["tenant_id"])
    op.create_index("ix_kachu_content_plans_source_conversation_id", "kachu_content_plans", ["source_conversation_id"])
    op.create_index("ix_kachu_content_plans_created_by_line_user_id", "kachu_content_plans", ["created_by_line_user_id"])
    op.create_index("ix_kachu_content_plans_status", "kachu_content_plans", ["status"])
    op.create_index("ix_kachu_content_plans_created_at", "kachu_content_plans", ["created_at"])
    op.create_index("ix_kachu_content_plans_updated_at", "kachu_content_plans", ["updated_at"])

    op.create_table(
        "kachu_content_plan_items",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("content_plan_id", sa.Text(), nullable=False),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
        sa.Column("workflow_type", sa.Text(), nullable=False, server_default="kachu_planned_content"),
        sa.Column("selected_platforms_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("draft_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="planned"),
        sa.Column("scheduled_for", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("pending_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kachu_content_plan_items_content_plan_id", "kachu_content_plan_items", ["content_plan_id"])
    op.create_index("ix_kachu_content_plan_items_tenant_id", "kachu_content_plan_items", ["tenant_id"])
    op.create_index("ix_kachu_content_plan_items_workflow_type", "kachu_content_plan_items", ["workflow_type"])
    op.create_index("ix_kachu_content_plan_items_status", "kachu_content_plan_items", ["status"])
    op.create_index("ix_kachu_content_plan_items_scheduled_for", "kachu_content_plan_items", ["scheduled_for"])
    op.create_index("ix_kachu_content_plan_items_pending_run_id", "kachu_content_plan_items", ["pending_run_id"])
    op.create_index("ix_kachu_content_plan_items_created_at", "kachu_content_plan_items", ["created_at"])
    op.create_index("ix_kachu_content_plan_items_updated_at", "kachu_content_plan_items", ["updated_at"])

    op.create_table(
        "kachu_external_engagements",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False, server_default="meta"),
        sa.Column("engagement_type", sa.Text(), nullable=False, server_default="comment"),
        sa.Column("external_thread_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("external_message_id", sa.Text(), nullable=False),
        sa.Column("author_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("author_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.Text(), nullable=False, server_default="received"),
        sa.Column("reply_draft", sa.Text(), nullable=False, server_default=""),
        sa.Column("related_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("external_message_id", name="uq_kachu_external_engagements_external_message_id"),
    )
    op.create_index("ix_kachu_external_engagements_tenant_id", "kachu_external_engagements", ["tenant_id"])
    op.create_index("ix_kachu_external_engagements_platform", "kachu_external_engagements", ["platform"])
    op.create_index("ix_kachu_external_engagements_engagement_type", "kachu_external_engagements", ["engagement_type"])
    op.create_index("ix_kachu_external_engagements_external_thread_id", "kachu_external_engagements", ["external_thread_id"])
    op.create_index("ix_kachu_external_engagements_external_message_id", "kachu_external_engagements", ["external_message_id"])
    op.create_index("ix_kachu_external_engagements_author_id", "kachu_external_engagements", ["author_id"])
    op.create_index("ix_kachu_external_engagements_status", "kachu_external_engagements", ["status"])
    op.create_index("ix_kachu_external_engagements_related_run_id", "kachu_external_engagements", ["related_run_id"])
    op.create_index("ix_kachu_external_engagements_created_at", "kachu_external_engagements", ["created_at"])
    op.create_index("ix_kachu_external_engagements_updated_at", "kachu_external_engagements", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_kachu_external_engagements_updated_at", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_created_at", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_related_run_id", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_status", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_author_id", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_external_message_id", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_external_thread_id", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_engagement_type", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_platform", table_name="kachu_external_engagements")
    op.drop_index("ix_kachu_external_engagements_tenant_id", table_name="kachu_external_engagements")
    op.drop_table("kachu_external_engagements")

    op.drop_index("ix_kachu_content_plan_items_updated_at", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_created_at", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_pending_run_id", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_scheduled_for", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_status", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_workflow_type", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_tenant_id", table_name="kachu_content_plan_items")
    op.drop_index("ix_kachu_content_plan_items_content_plan_id", table_name="kachu_content_plan_items")
    op.drop_table("kachu_content_plan_items")

    op.drop_index("ix_kachu_content_plans_updated_at", table_name="kachu_content_plans")
    op.drop_index("ix_kachu_content_plans_created_at", table_name="kachu_content_plans")
    op.drop_index("ix_kachu_content_plans_status", table_name="kachu_content_plans")
    op.drop_index("ix_kachu_content_plans_created_by_line_user_id", table_name="kachu_content_plans")
    op.drop_index("ix_kachu_content_plans_source_conversation_id", table_name="kachu_content_plans")
    op.drop_index("ix_kachu_content_plans_tenant_id", table_name="kachu_content_plans")
    op.drop_table("kachu_content_plans")