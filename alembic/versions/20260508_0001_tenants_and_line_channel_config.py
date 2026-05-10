"""initial: kachu_tenants + kachu_line_channel_configs

Revision ID: 20260508_0001
Revises:
Create Date: 2026-05-08 00:01:00

任務 1-1：建立 kachu_tenants 和 kachu_line_channel_configs（webhook config）。
schema 依據：§7.4 資料層核心 tables + Kachu_v2 TenantTable（含 Kachu+ 新增 sleep_threshold）
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "20260508_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_tenants",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, server_default=""),
        sa.Column("industry_type", sa.Text, nullable=False, server_default=""),
        sa.Column("address", sa.Text, nullable=False, server_default=""),
        sa.Column("timezone", sa.Text, nullable=False, server_default="Asia/Taipei"),
        sa.Column("plan", sa.Text, nullable=False, server_default="trial"),
        sa.Column("plan_expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("merchant_slug", sa.Text, nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        # Kachu+ 新增：模組三 sleep_since_days 計算基準，onboarding 時由商家設定
        sa.Column("sleep_threshold", sa.Integer, nullable=False, server_default="60"),
        sa.Column("quiet_hours_start", sa.Integer, nullable=True),
        sa.Column("quiet_hours_end", sa.Integer, nullable=True),
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

    op.create_table(
        "kachu_line_channel_configs",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "tenant_id",
            sa.Text,
            sa.ForeignKey("kachu_tenants.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # 憑證欄位：application layer 加密後存入，不明文儲存
        sa.Column("channel_access_token", sa.Text, nullable=False, server_default=""),
        sa.Column("channel_secret", sa.Text, nullable=False, server_default=""),
        sa.Column("channel_id", sa.Text, nullable=False, server_default=""),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
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
        # 渠道 R3：一個 tenant 只能綁定一組 LINE channel，防止憑證衝突
        sa.UniqueConstraint("tenant_id", name="uq_line_channel_configs_tenant_id"),
    )

    # Index for common lookup: active config by tenant
    op.create_index(
        "ix_line_channel_configs_tenant_id",
        "kachu_line_channel_configs",
        ["tenant_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_line_channel_configs_tenant_id", table_name="kachu_line_channel_configs")
    op.drop_table("kachu_line_channel_configs")
    op.drop_table("kachu_tenants")
