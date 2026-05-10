"""module 2 connector accounts

Revision ID: 20260509_0005
Revises: 20260509_0004
Create Date: 2026-05-09 02:10:00

任務 2-2：品牌陣地 connector credential 管理，供 Google 評論 adapter 使用。
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0005"
down_revision = "20260509_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_connector_accounts",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("platform", sa.Text, nullable=False),
        sa.Column("account_label", sa.Text, nullable=False, server_default=""),
        sa.Column("credentials_json", sa.Text, nullable=False, server_default="{}"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("last_refreshed_at", sa.TIMESTAMP(timezone=True), nullable=True),
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
        sa.UniqueConstraint("tenant_id", "platform", name="uq_connector_account_tenant_platform"),
    )
    op.create_index("ix_connector_accounts_tenant_id", "kachu_connector_accounts", ["tenant_id"])


def downgrade() -> None:
    op.drop_index("ix_connector_accounts_tenant_id", table_name="kachu_connector_accounts")
    op.drop_table("kachu_connector_accounts")