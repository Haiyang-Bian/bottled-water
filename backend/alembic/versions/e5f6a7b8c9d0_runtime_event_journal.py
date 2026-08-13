"""runtime durable event journal

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-13 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runtime_runs",
        sa.Column("last_event_sequence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("runtime_runs", sa.Column("journal_version", sa.Integer(), nullable=True))
    op.add_column(
        "runtime_runs",
        sa.Column("output", sa.Text(), nullable=False, server_default=""),
    )

    op.create_table(
        "runtime_events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("context_scope_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=120), nullable=False),
        sa.Column("source", sa.String(length=120), nullable=False),
        sa.Column("target", sa.String(length=120), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("causation_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("persisted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["context_scope_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("run_id", "sequence", name="uq_runtime_events_run_sequence"),
    )
    op.create_index("ix_runtime_events_context_scope_id", "runtime_events", ["context_scope_id"])
    op.create_index("ix_runtime_events_run_id", "runtime_events", ["run_id"])
    op.create_index("ix_runtime_events_type", "runtime_events", ["type"])

    op.create_table(
        "runtime_event_consumers",
        sa.Column("consumer_name", sa.String(length=120), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("last_sequence", sa.Integer(), nullable=False),
        sa.Column("last_event_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["run_id"], ["runtime_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("consumer_name", "run_id"),
    )


def downgrade() -> None:
    op.drop_table("runtime_event_consumers")
    op.drop_index("ix_runtime_events_type", table_name="runtime_events")
    op.drop_index("ix_runtime_events_run_id", table_name="runtime_events")
    op.drop_index("ix_runtime_events_context_scope_id", table_name="runtime_events")
    op.drop_table("runtime_events")
    op.drop_column("runtime_runs", "output")
    op.drop_column("runtime_runs", "journal_version")
    op.drop_column("runtime_runs", "last_event_sequence")
