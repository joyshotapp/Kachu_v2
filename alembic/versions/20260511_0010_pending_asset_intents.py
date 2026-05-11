"""pending asset intents for LINE asset guidance

Revision ID: 20260511_0010
Revises: 20260510_0015
Create Date: 2026-05-11 10:40:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260511_0010"
down_revision = "20260510_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_pending_asset_intents",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("line_message_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("asset_type", sa.Text(), nullable=False, server_default="image"),
        sa.Column("payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("selected_decision", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("resolved_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index("ix_kachu_pending_asset_intents_tenant_id", "kachu_pending_asset_intents", ["tenant_id"])
    op.create_index("ix_kachu_pending_asset_intents_line_user_id", "kachu_pending_asset_intents", ["line_user_id"])
    op.create_index("ix_kachu_pending_asset_intents_line_message_id", "kachu_pending_asset_intents", ["line_message_id"])
    op.create_index("ix_kachu_pending_asset_intents_asset_type", "kachu_pending_asset_intents", ["asset_type"])
    op.create_index("ix_kachu_pending_asset_intents_status", "kachu_pending_asset_intents", ["status"])
    op.create_index("ix_kachu_pending_asset_intents_selected_decision", "kachu_pending_asset_intents", ["selected_decision"])
    op.create_index("ix_kachu_pending_asset_intents_expires_at", "kachu_pending_asset_intents", ["expires_at"])
    op.create_index("ix_kachu_pending_asset_intents_created_at", "kachu_pending_asset_intents", ["created_at"])
    op.create_index("ix_kachu_pending_asset_intents_updated_at", "kachu_pending_asset_intents", ["updated_at"])
    op.create_index("ix_kachu_pending_asset_intents_resolved_at", "kachu_pending_asset_intents", ["resolved_at"])


def downgrade() -> None:
    op.drop_index("ix_kachu_pending_asset_intents_resolved_at", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_updated_at", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_created_at", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_expires_at", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_selected_decision", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_status", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_asset_type", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_line_message_id", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_line_user_id", table_name="kachu_pending_asset_intents")
    op.drop_index("ix_kachu_pending_asset_intents_tenant_id", table_name="kachu_pending_asset_intents")
    op.drop_table("kachu_pending_asset_intents")
