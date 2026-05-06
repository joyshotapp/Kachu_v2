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


def _column_names(connection: sa.Connection, table_name: str) -> set[str]:
    inspector = sa.inspect(connection)
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    connection = op.get_bind()
    existing_columns = _column_names(connection, "kachu_tenant_automation_settings")
    if "meta_post_enabled" not in existing_columns:
        op.add_column(
            "kachu_tenant_automation_settings",
            sa.Column("meta_post_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        )
    if "meta_post_frequency" not in existing_columns:
        op.add_column(
            "kachu_tenant_automation_settings",
            sa.Column("meta_post_frequency", sa.String(), nullable=False, server_default="weekly"),
        )
    if "meta_post_weekday" not in existing_columns:
        op.add_column(
            "kachu_tenant_automation_settings",
            sa.Column("meta_post_weekday", sa.String(), nullable=False, server_default="fri"),
        )
    if "meta_post_hour" not in existing_columns:
        op.add_column(
            "kachu_tenant_automation_settings",
            sa.Column("meta_post_hour", sa.Integer(), nullable=False, server_default="11"),
        )


def downgrade() -> None:
    connection = op.get_bind()
    existing_columns = _column_names(connection, "kachu_tenant_automation_settings")
    if "meta_post_hour" in existing_columns:
        op.drop_column("kachu_tenant_automation_settings", "meta_post_hour")
    if "meta_post_weekday" in existing_columns:
        op.drop_column("kachu_tenant_automation_settings", "meta_post_weekday")
    if "meta_post_frequency" in existing_columns:
        op.drop_column("kachu_tenant_automation_settings", "meta_post_frequency")
    if "meta_post_enabled" in existing_columns:
        op.drop_column("kachu_tenant_automation_settings", "meta_post_enabled")