"""module 3 manual tags and timeline

Revision ID: 20260509_0004
Revises: 20260509_0003
Create Date: 2026-05-09 01:10:00

任務 3-3：手動標籤 CRUD，且刪除 tag 不破壞歷史 timeline。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0004"
down_revision = "20260509_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_customer_tag_definitions",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("color", sa.Text, nullable=False, server_default=""),
        sa.Column("source", sa.Text, nullable=False, server_default="manual"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "name", name="uq_customer_tag_definition_tenant_name"),
    )
    op.create_index("ix_customer_tag_definitions_tenant_id", "kachu_customer_tag_definitions", ["tenant_id"])

    op.create_table(
        "kachu_customer_tag_assignments",
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
            "tag_id",
            sa.Text,
            sa.ForeignKey("kachu_customer_tag_definitions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("applied_source", sa.Text, nullable=False, server_default="manual"),
        sa.Column(
            "applied_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("removed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
            "profile_id",
            "tag_id",
            name="uq_customer_tag_assignment_tenant_profile_tag",
        ),
    )
    op.create_index("ix_customer_tag_assignments_profile_id", "kachu_customer_tag_assignments", ["profile_id"])
    op.create_index("ix_customer_tag_assignments_tag_id", "kachu_customer_tag_assignments", ["tag_id"])
    op.create_index("ix_customer_tag_assignments_tenant_id", "kachu_customer_tag_assignments", ["tenant_id"])

    op.create_table(
        "kachu_customer_timeline_events",
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
        sa.Column("activity_type", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("payload_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index("ix_customer_timeline_events_profile_id", "kachu_customer_timeline_events", ["profile_id"])
    op.create_index("ix_customer_timeline_events_tenant_id", "kachu_customer_timeline_events", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_customer_timeline_events_tenant_id", table_name="kachu_customer_timeline_events")
    op.drop_index("ix_customer_timeline_events_profile_id", table_name="kachu_customer_timeline_events")
    op.drop_table("kachu_customer_timeline_events")

    op.drop_index("ix_customer_tag_assignments_tenant_id", table_name="kachu_customer_tag_assignments")
    op.drop_index("ix_customer_tag_assignments_tag_id", table_name="kachu_customer_tag_assignments")
    op.drop_index("ix_customer_tag_assignments_profile_id", table_name="kachu_customer_tag_assignments")
    op.drop_table("kachu_customer_tag_assignments")

    op.drop_index("ix_customer_tag_definitions_tenant_id", table_name="kachu_customer_tag_definitions")
    op.drop_table("kachu_customer_tag_definitions")