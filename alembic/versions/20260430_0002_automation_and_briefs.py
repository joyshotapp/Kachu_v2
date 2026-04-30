"""automation settings and brief support

Revision ID: 20260430_0002
Revises: 20260427_0001
Create Date: 2026-04-30 00:02:00
"""
from __future__ import annotations

from alembic import op
from sqlmodel import SQLModel

from kachu.persistence import tables  # noqa: F401

# revision identifiers, used by Alembic.
revision = "20260430_0002"
down_revision = "20260427_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_context().connection
    SQLModel.metadata.create_all(bind=connection)


def downgrade() -> None:
    op.drop_table("kachu_deferred_dispatches")
    op.drop_table("kachu_tenant_automation_settings")