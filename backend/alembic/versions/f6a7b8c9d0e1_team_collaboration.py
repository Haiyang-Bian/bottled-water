"""durable team collaboration messages

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-19 10:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_team_settings",
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("summary_agent_id", sa.String(length=36), nullable=True),
        sa.Column("live_user_input", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("max_messages", sa.Integer(), nullable=False, server_default="64"),
        sa.Column("max_agent_turns", sa.Integer(), nullable=False, server_default="12"),
        sa.Column("max_open_threads", sa.Integer(), nullable=False, server_default="24"),
        sa.Column("max_message_chars", sa.Integer(), nullable=False, server_default="8000"),
        sa.Column("last_message_sequence", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["summary_agent_id"], ["agents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_table(
        "runtime_team_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("sender_type", sa.String(length=20), nullable=False),
        sa.Column("sender_id", sa.String(length=64), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("recipient_agent_ids", sa.JSON(), nullable=False),
        sa.Column("thread_id", sa.String(length=64), nullable=True),
        sa.Column("reply_to_message_id", sa.String(length=36), nullable=True),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("expects_reply", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("consumed_by", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id", "sequence", name="uq_runtime_team_messages_conversation_sequence"
        ),
    )
    op.create_index(
        "ix_runtime_team_messages_conversation_id", "runtime_team_messages", ["conversation_id"]
    )
    op.create_index("ix_runtime_team_messages_run_id", "runtime_team_messages", ["run_id"])
    op.create_index("ix_runtime_team_messages_status", "runtime_team_messages", ["status"])
    op.create_index("ix_runtime_team_messages_thread_id", "runtime_team_messages", ["thread_id"])


def downgrade() -> None:
    op.drop_index("ix_runtime_team_messages_thread_id", table_name="runtime_team_messages")
    op.drop_index("ix_runtime_team_messages_status", table_name="runtime_team_messages")
    op.drop_index("ix_runtime_team_messages_run_id", table_name="runtime_team_messages")
    op.drop_index("ix_runtime_team_messages_conversation_id", table_name="runtime_team_messages")
    op.drop_table("runtime_team_messages")
    op.drop_table("conversation_team_settings")
