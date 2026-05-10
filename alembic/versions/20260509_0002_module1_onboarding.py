"""module 1 onboarding: kachu_onboarding_states + kachu_knowledge_entries

Revision ID: 20260509_0002
Revises: 20260508_0001
Create Date: 2026-05-09 00:01:00

任務 1-4：建立 onboarding 狀態機 table 和 knowledge entries table。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0002"
down_revision = "20260508_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_onboarding_states",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("step", sa.Text, nullable=False, server_default="new"),
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
        sa.UniqueConstraint("tenant_id", name="uq_onboarding_states_tenant_id"),
    )
    op.create_index(
        "ix_onboarding_states_tenant_id",
        "kachu_onboarding_states",
        ["tenant_id"],
    )

    op.create_table(
        "kachu_knowledge_entries",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("category", sa.Text, nullable=False, server_default=""),
        sa.Column("content", sa.Text, nullable=False, server_default=""),
        sa.Column("source_type", sa.Text, nullable=False, server_default="conversation"),
        sa.Column(
            "created_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_knowledge_entries_tenant_id",
        "kachu_knowledge_entries",
        ["tenant_id"],
    )
    op.create_index(
        "ix_knowledge_entries_tenant_category",
        "kachu_knowledge_entries",
        ["tenant_id", "category"],
    )


def downgrade() -> None:
    op.drop_index("ix_knowledge_entries_tenant_category", table_name="kachu_knowledge_entries")
    op.drop_index("ix_knowledge_entries_tenant_id", table_name="kachu_knowledge_entries")
    op.drop_table("kachu_knowledge_entries")
    op.drop_index("ix_onboarding_states_tenant_id", table_name="kachu_onboarding_states")
    op.drop_table("kachu_onboarding_states")
