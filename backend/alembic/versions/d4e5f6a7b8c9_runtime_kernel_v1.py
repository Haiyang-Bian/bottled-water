"""runtime kernel v1 context scopes and runs

Revision ID: d4e5f6a7b8c9
Revises: c7a8b9d0e1f2
Create Date: 2026-08-13 00:00:00.000000
"""

from __future__ import annotations

import json
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, None] = "c7a8b9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "conversations",
        "active_session_id",
        new_column_name="active_run_id",
        existing_type=sa.String(length=36),
        existing_nullable=True,
    )
    op.create_table(
        "runtime_context_states",
        sa.Column("context_scope_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("messages", sa.JSON(), nullable=False),
        sa.Column("blackboard", sa.JSON(), nullable=False),
        sa.Column("agent_memories", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["context_scope_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("context_scope_id"),
    )
    op.create_table(
        "runtime_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("context_scope_id", sa.String(length=36), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("reason_code", sa.String(length=80), nullable=True),
        sa.Column("input_preview", sa.Text(), nullable=False),
        sa.Column("limits", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("context_version", sa.Integer(), nullable=False),
        sa.Column("metadata", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["context_scope_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_runtime_runs_context_scope_id", "runtime_runs", ["context_scope_id"])
    op.create_index("ix_runtime_runs_state", "runtime_runs", ["state"])
    _migrate_conversation_runtime_state()


def _migrate_conversation_runtime_state() -> None:
    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, metadata FROM conversations")).mappings()
    now = connection.execute(sa.text("SELECT CURRENT_TIMESTAMP")).scalar()
    for row in rows:
        raw = row["metadata"]
        metadata = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        blackboard = metadata.pop("blackboard", {})
        metadata.pop("runtime_mode", None)
        metadata.pop("agent_contexts", None)
        metadata.pop("agent_context", None)
        runtime = metadata.get("runtime")
        if isinstance(runtime, dict):
            runtime.pop("runtime_mode", None)
            for generation in runtime.get("generations") or []:
                if isinstance(generation, dict):
                    generation.pop("runtime_mode", None)
        connection.execute(
            sa.text("UPDATE conversations SET metadata = :metadata WHERE id = :id").bindparams(
                sa.bindparam("metadata", type_=sa.JSON())
            ),
            {"id": row["id"], "metadata": metadata},
        )
        connection.execute(
            sa.text(
                "INSERT INTO runtime_context_states "
                "(context_scope_id, version, messages, blackboard, agent_memories, created_at, updated_at) "
                "VALUES (:id, :version, :messages, :blackboard, :memories, :now, :now)"
            ).bindparams(
                sa.bindparam("messages", type_=sa.JSON()),
                sa.bindparam("blackboard", type_=sa.JSON()),
                sa.bindparam("memories", type_=sa.JSON()),
            ),
            {
                "id": row["id"],
                "version": int(blackboard.get("version") or 0) if isinstance(blackboard, dict) else 0,
                "messages": [],
                "blackboard": blackboard if isinstance(blackboard, dict) else {},
                "memories": {},
                "now": now,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_runtime_runs_state", table_name="runtime_runs")
    op.drop_index("ix_runtime_runs_context_scope_id", table_name="runtime_runs")
    op.drop_table("runtime_runs")
    op.drop_table("runtime_context_states")
    op.alter_column(
        "conversations",
        "active_run_id",
        new_column_name="active_session_id",
        existing_type=sa.String(length=36),
        existing_nullable=True,
    )
