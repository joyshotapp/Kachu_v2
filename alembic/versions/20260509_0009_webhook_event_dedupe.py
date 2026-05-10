"""webhook event dedupe and raw payload audit

Revision ID: 20260509_0009
Revises: 20260509_0008
Create Date: 2026-05-09 11:05:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0009"
down_revision = "20260509_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_webhook_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("provider", sa.Text(), nullable=False, server_default="line"),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False, server_default=""),
        sa.Column("raw_payload_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "provider", "dedupe_key", name="uq_kachu_webhook_event_dedupe"),
    )
    op.create_index("ix_kachu_webhook_events_tenant_id", "kachu_webhook_events", ["tenant_id"])
    op.create_index("ix_kachu_webhook_events_provider", "kachu_webhook_events", ["provider"])
    op.create_index("ix_kachu_webhook_events_dedupe_key", "kachu_webhook_events", ["dedupe_key"])
    op.create_index("ix_kachu_webhook_events_event_type", "kachu_webhook_events", ["event_type"])


def downgrade() -> None:
    op.drop_index("ix_kachu_webhook_events_event_type", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_dedupe_key", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_provider", table_name="kachu_webhook_events")
    op.drop_index("ix_kachu_webhook_events_tenant_id", table_name="kachu_webhook_events")
    op.drop_table("kachu_webhook_events")