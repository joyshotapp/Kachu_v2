"""expand webhook events into shared event envelope

Revision ID: 20260510_0015
Revises: 20260510_0014
Create Date: 2026-05-10 22:35:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_0015"
down_revision = "20260510_0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("kachu_webhook_events", sa.Column("external_event_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("kachu_webhook_events", sa.Column("external_user_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("kachu_webhook_events", sa.Column("external_thread_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("kachu_webhook_events", sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=True))
    op.add_column("kachu_webhook_events", sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")))
    op.create_index("ix_kachu_webhook_events_external_event_id", "kachu_webhook_events", ["external_event_id"])
    op.create_index("ix_kachu_webhook_events_external_user_id", "kachu_webhook_events", ["external_user_id"])
    op.create_index("ix_kachu_webhook_events_external_thread_id", "kachu_webhook_events", ["external_thread_id"])
    op.create_index("ix_kachu_webhook_events_occurred_at", "kachu_webhook_events", ["occurred_at"])
    op.create_index("ix_kachu_webhook_events_received_at", "kachu_webhook_events", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_kachu_webhook_events_received_at", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_occurred_at", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_external_thread_id", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_external_user_id", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_external_event_id", table_name="kachu_webhook_events")
    op.drop_column("kachu_webhook_events", "received_at")
    op.drop_column("kachu_webhook_events", "occurred_at")
    op.drop_column("kachu_webhook_events", "external_thread_id")
    op.drop_column("kachu_webhook_events", "external_user_id")
    op.drop_column("kachu_webhook_events", "external_event_id")