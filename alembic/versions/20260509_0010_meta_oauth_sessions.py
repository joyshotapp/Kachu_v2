"""meta oauth sessions

Revision ID: 20260509_0010
Revises: 20260509_0009
Create Date: 2026-05-09 18:20:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260509_0010"
down_revision = "20260509_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_meta_oauth_sessions",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default="pending"),
        sa.Column("requested_platform", sa.Text(), nullable=False, server_default="meta"),
        sa.Column("page_candidates_json", sa.Text(), nullable=False, server_default="[]"),
        sa.Column("selected_page_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("selected_page_name", sa.Text(), nullable=False, server_default=""),
        sa.Column("selected_ig_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("user_access_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("fb_page_access_token", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_code", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("completed_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("state", name="uq_kachu_meta_oauth_session_state"),
    )
    op.create_index("ix_kachu_meta_oauth_sessions_tenant_id", "kachu_meta_oauth_sessions", ["tenant_id"])
    op.create_index("ix_kachu_meta_oauth_sessions_line_user_id", "kachu_meta_oauth_sessions", ["line_user_id"])
    op.create_index("ix_kachu_meta_oauth_sessions_state", "kachu_meta_oauth_sessions", ["state"])
    op.create_index("ix_kachu_meta_oauth_sessions_status", "kachu_meta_oauth_sessions", ["status"])
    op.create_index("ix_kachu_meta_oauth_sessions_expires_at", "kachu_meta_oauth_sessions", ["expires_at"])


def downgrade() -> None:
    op.drop_index("ix_kachu_meta_oauth_sessions_expires_at", table_name="kachu_meta_oauth_sessions")
    op.drop_index("ix_kachu_meta_oauth_sessions_status", table_name="kachu_meta_oauth_sessions")
    op.drop_index("ix_kachu_meta_oauth_sessions_state", table_name="kachu_meta_oauth_sessions")
    op.drop_index("ix_kachu_meta_oauth_sessions_line_user_id", table_name="kachu_meta_oauth_sessions")
    op.drop_index("ix_kachu_meta_oauth_sessions_tenant_id", table_name="kachu_meta_oauth_sessions")
    op.drop_table("kachu_meta_oauth_sessions")