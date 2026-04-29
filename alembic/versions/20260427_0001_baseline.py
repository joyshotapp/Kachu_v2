"""baseline schema

Revision ID: 20260427_0001
Revises:
Create Date: 2026-04-27 00:01:00
"""
from __future__ import annotations

from alembic import op
from sqlmodel import SQLModel

from kachu.persistence import tables  # noqa: F401

# revision identifiers, used by Alembic.
revision = "20260427_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_context().connection
    SQLModel.metadata.create_all(bind=connection)


def downgrade() -> None:
    connection = op.get_context().connection
    SQLModel.metadata.drop_all(bind=connection)