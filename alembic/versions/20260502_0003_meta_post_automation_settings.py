"""add meta post automation settings

Revision ID: 20260502_0003
Revises: 20260430_0002
Create Date: 2026-05-02 10:00:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = "20260502_0003"
down_revision = "20260430_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "kachu_tenant_automation_settings",
        sa.Column("meta_post_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.add_column(
        "kachu_tenant_automation_settings",
        sa.Column("meta_post_frequency", sa.String(), nullable=False, server_default="weekly"),
    )
    op.add_column(
        "kachu_tenant_automation_settings",
        sa.Column("meta_post_weekday", sa.String(), nullable=False, server_default="fri"),
    )
    op.add_column(
        "kachu_tenant_automation_settings",
        sa.Column("meta_post_hour", sa.Integer(), nullable=False, server_default="11"),
    )


def downgrade() -> None:
    op.drop_column("kachu_tenant_automation_settings", "meta_post_hour")
    op.drop_column("kachu_tenant_automation_settings", "meta_post_weekday")
    op.drop_column("kachu_tenant_automation_settings", "meta_post_frequency")
    op.drop_column("kachu_tenant_automation_settings", "meta_post_enabled")