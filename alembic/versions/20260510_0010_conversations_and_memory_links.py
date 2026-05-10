"""add conversations and memory traceability

Revision ID: 20260510_0010
Revises: 20260509_0009
Create Date: 2026-05-10 15:30:00
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260510_0010"
down_revision = "20260509_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "kachu_conversations",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("tenant_id", sa.Text(), nullable=False),
        sa.Column("line_user_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor_role", sa.Text(), nullable=False, server_default=""),
        sa.Column("channel_type", sa.Text(), nullable=False, server_default="line"),
        sa.Column("conversation_kind", sa.Text(), nullable=False, server_default=""),
        sa.Column("content_text", sa.Text(), nullable=False, server_default=""),
        sa.Column("source_message_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("related_task_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("related_run_id", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.create_index("ix_kachu_conversations_tenant_id", "kachu_conversations", ["tenant_id"])
    op.create_index("ix_kachu_conversations_line_user_id", "kachu_conversations", ["line_user_id"])
    op.create_index("ix_kachu_conversations_actor_role", "kachu_conversations", ["actor_role"])
    op.create_index("ix_kachu_conversations_channel_type", "kachu_conversations", ["channel_type"])
    op.create_index("ix_kachu_conversations_conversation_kind", "kachu_conversations", ["conversation_kind"])
    op.create_index("ix_kachu_conversations_source_message_id", "kachu_conversations", ["source_message_id"])
    op.create_index("ix_kachu_conversations_related_task_id", "kachu_conversations", ["related_task_id"])
    op.create_index("ix_kachu_conversations_related_run_id", "kachu_conversations", ["related_run_id"])
    op.create_index("ix_kachu_conversations_created_at", "kachu_conversations", ["created_at"])

    op.add_column("kachu_knowledge_entries", sa.Column("source_conversation_id", sa.Text(), nullable=False, server_default=""))
    op.add_column("kachu_knowledge_entries", sa.Column("status", sa.Text(), nullable=False, server_default="active"))
    op.add_column("kachu_knowledge_entries", sa.Column("confidence_score", sa.Float(), nullable=False, server_default="1.0"))
    op.add_column("kachu_knowledge_entries", sa.Column("supersedes_entry_id", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_kachu_knowledge_entries_source_conversation_id", "kachu_knowledge_entries", ["source_conversation_id"])
    op.create_index("ix_kachu_knowledge_entries_status", "kachu_knowledge_entries", ["status"])
    op.create_index("ix_kachu_knowledge_entries_supersedes_entry_id", "kachu_knowledge_entries", ["supersedes_entry_id"])

    op.add_column("kachu_execute_task_records", sa.Column("related_conversation_id", sa.Text(), nullable=False, server_default=""))
    op.create_index("ix_kachu_execute_task_records_related_conversation_id", "kachu_execute_task_records", ["related_conversation_id"])


def downgrade() -> None:
    op.drop_index("ix_kachu_execute_task_records_related_conversation_id", table_name="kachu_execute_task_records")
    op.drop_column("kachu_execute_task_records", "related_conversation_id")

    op.drop_index("ix_kachu_knowledge_entries_supersedes_entry_id", table_name="kachu_knowledge_entries")
    op.drop_index("ix_kachu_knowledge_entries_status", table_name="kachu_knowledge_entries")
    op.drop_index("ix_kachu_knowledge_entries_source_conversation_id", table_name="kachu_knowledge_entries")
    op.drop_column("kachu_knowledge_entries", "supersedes_entry_id")
    op.drop_column("kachu_knowledge_entries", "confidence_score")
    op.drop_column("kachu_knowledge_entries", "status")
    op.drop_column("kachu_knowledge_entries", "source_conversation_id")

    op.drop_index("ix_kachu_conversations_created_at", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_related_run_id", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_related_task_id", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_source_message_id", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_conversation_kind", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_channel_type", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_actor_role", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_line_user_id", table_name="kachu_conversations")
    op.drop_index("ix_kachu_conversations_tenant_id", table_name="kachu_conversations")
    op.drop_table("kachu_conversations")