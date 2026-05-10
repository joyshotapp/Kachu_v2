"""add profile merge audit and handoff locks

Revision ID: 20260510_0014
Revises: 20260510_0013
Create Date: 2026-05-10 21:40:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_0014"
down_revision = "20260510_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kachu_customer_profiles",
        sa.Column("merged_into_profile_id", sa.Text(), nullable=False, server_default=""),
    )
    op.create_index(
        "ix_kachu_customer_profiles_merged_into_profile_id",
        "kachu_customer_profiles",
        ["merged_into_profile_id"],
    )

    op.create_table(
        "kachu_profile_merge_audits",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("source_profile_id", sa.Text(), nullable=False),
        sa.Column("target_profile_id", sa.Text(), nullable=False),
        sa.Column("actor_line_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("summary_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kachu_profile_merge_audits_tenant_id", "kachu_profile_merge_audits", ["tenant_id"])
    op.create_index("ix_kachu_profile_merge_audits_source_profile_id", "kachu_profile_merge_audits", ["source_profile_id"])
    op.create_index("ix_kachu_profile_merge_audits_target_profile_id", "kachu_profile_merge_audits", ["target_profile_id"])
    op.create_index("ix_kachu_profile_merge_audits_actor_line_id", "kachu_profile_merge_audits", ["actor_line_id"])
    op.create_index("ix_kachu_profile_merge_audits_created_at", "kachu_profile_merge_audits", ["created_at"])

    op.create_table(
        "kachu_conversation_handoff_locks",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("channel_type", sa.Text(), nullable=False, server_default="line"),
        sa.Column("external_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default="human_handoff"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("locked_by_line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("released_by_line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("locked_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("released_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kachu_conversation_handoff_locks_tenant_id", "kachu_conversation_handoff_locks", ["tenant_id"])
    op.create_index("ix_kachu_conversation_handoff_locks_channel_type", "kachu_conversation_handoff_locks", ["channel_type"])
    op.create_index("ix_kachu_conversation_handoff_locks_external_user_id", "kachu_conversation_handoff_locks", ["external_user_id"])
    op.create_index("ix_kachu_conversation_handoff_locks_is_active", "kachu_conversation_handoff_locks", ["is_active"])
    op.create_index("ix_kachu_conversation_handoff_locks_locked_by_line_user_id", "kachu_conversation_handoff_locks", ["locked_by_line_user_id"])
    op.create_index("ix_kachu_conversation_handoff_locks_released_by_line_user_id", "kachu_conversation_handoff_locks", ["released_by_line_user_id"])
    op.create_index("ix_kachu_conversation_handoff_locks_locked_at", "kachu_conversation_handoff_locks", ["locked_at"])
    op.create_index("ix_kachu_conversation_handoff_locks_released_at", "kachu_conversation_handoff_locks", ["released_at"])
    op.create_index("ix_kachu_conversation_handoff_locks_updated_at", "kachu_conversation_handoff_locks", ["updated_at"])


def downgrade() -> None:
    op.drop_index("ix_kachu_conversation_handoff_locks_updated_at", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_released_at", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_locked_at", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_released_by_line_user_id", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_locked_by_line_user_id", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_is_active", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_external_user_id", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_channel_type", table_name="kachu_conversation_handoff_locks")
    op.drop_index("ix_kachu_conversation_handoff_locks_tenant_id", table_name="kachu_conversation_handoff_locks")
    op.drop_table("kachu_conversation_handoff_locks")

    op.drop_index("ix_kachu_profile_merge_audits_created_at", table_name="kachu_profile_merge_audits")
    op.drop_index("ix_kachu_profile_merge_audits_actor_line_id", table_name="kachu_profile_merge_audits")
    op.drop_index("ix_kachu_profile_merge_audits_target_profile_id", table_name="kachu_profile_merge_audits")
    op.drop_index("ix_kachu_profile_merge_audits_source_profile_id", table_name="kachu_profile_merge_audits")
    op.drop_index("ix_kachu_profile_merge_audits_tenant_id", table_name="kachu_profile_merge_audits")
    op.drop_table("kachu_profile_merge_audits")

    op.drop_index("ix_kachu_customer_profiles_merged_into_profile_id", table_name="kachu_customer_profiles")
    op.drop_column("kachu_customer_profiles", "merged_into_profile_id")