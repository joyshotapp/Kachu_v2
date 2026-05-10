"""tenant memberships for owner recipients

Revision ID: 20260509_0007
Revises: 20260509_0006
Create Date: 2026-05-09 05:50:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0007"
down_revision = "20260509_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_tenant_memberships",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("tenant_id", sa.Text, nullable=False),
        sa.Column("line_user_id", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False, server_default="owner"),
        sa.Column("display_name", sa.Text, nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_tenant_memberships_tenant_id", "kachu_tenant_memberships", ["tenant_id"])
    op.create_index("ix_tenant_memberships_line_user_id", "kachu_tenant_memberships", ["line_user_id"])


def downgrade() -> None:
    op.drop_index("ix_tenant_memberships_line_user_id", table_name="kachu_tenant_memberships")
    op.drop_index("ix_tenant_memberships_tenant_id", table_name="kachu_tenant_memberships")
    op.drop_table("kachu_tenant_memberships")