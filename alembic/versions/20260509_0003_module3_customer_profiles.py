"""module 3 identity foundation: customer profiles + channel entities + profile links

Revision ID: 20260509_0003
Revises: 20260509_0002
Create Date: 2026-05-09 00:20:00

任務 3-1：建立三層 identity tables，供顧客記憶與沉睡查詢使用。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0003"
down_revision = "20260509_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_customer_profiles",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text, nullable=False, server_default=""),
        sa.Column("custom_name", sa.Text, nullable=False, server_default=""),
        sa.Column("status", sa.Text, nullable=False, server_default="active"),
        sa.Column("last_interaction_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("interaction_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("sleep_since_days", sa.Integer, nullable=False, server_default="0"),
        sa.Column("opt_out", sa.Boolean, nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_customer_profiles_tenant_id", "kachu_customer_profiles", ["tenant_id"])
    op.create_index("ix_customer_profiles_sleep_since_days", "kachu_customer_profiles", ["sleep_since_days"])

    op.create_table(
        "kachu_channel_entities",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("channel_type", sa.Text, nullable=False, server_default="line"),
        sa.Column("external_user_id", sa.Text, nullable=False, server_default=""),
        sa.Column("reachability_status", sa.Text, nullable=False, server_default="reachable"),
        sa.Column("occurred_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("received_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "channel_type",
            "external_user_id",
            name="uq_channel_entities_tenant_channel_external_user",
        ),
    )
    op.create_index("ix_channel_entities_tenant_id", "kachu_channel_entities", ["tenant_id"])
    op.create_index("ix_channel_entities_external_user_id", "kachu_channel_entities", ["external_user_id"])

    op.create_table(
        "kachu_profile_links",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "profile_id",
            sa.Text,
            sa.ForeignKey("kachu_customer_profiles.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "channel_entity_id",
            sa.Text,
            sa.ForeignKey("kachu_channel_entities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("confidence_score", sa.Numeric(5, 4), nullable=False, server_default="1.0"),
        sa.Column("resolution_source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("resolution_note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("tenant_id", "channel_entity_id", name="uq_profile_links_tenant_channel_entity"),
    )
    op.create_index("ix_profile_links_tenant_id", "kachu_profile_links", ["tenant_id"])
    op.create_index("ix_profile_links_profile_id", "kachu_profile_links", ["profile_id"])


def downgrade() -> None:
    op.drop_index("ix_profile_links_profile_id", table_name="kachu_profile_links")
    op.drop_index("ix_profile_links_tenant_id", table_name="kachu_profile_links")
    op.drop_table("kachu_profile_links")

    op.drop_index("ix_channel_entities_external_user_id", table_name="kachu_channel_entities")
    op.drop_index("ix_channel_entities_tenant_id", table_name="kachu_channel_entities")
    op.drop_table("kachu_channel_entities")

    op.drop_index("ix_customer_profiles_sleep_since_days", table_name="kachu_customer_profiles")
    op.drop_index("ix_customer_profiles_tenant_id", table_name="kachu_customer_profiles")
    op.drop_table("kachu_customer_profiles")