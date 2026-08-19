"""conversation repositories and agent worktrees

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-08-19 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "conversation_repositories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("repository_path", sa.Text(), nullable=False),
        sa.Column("git_common_dir", sa.Text(), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column(
            "require_user_approval", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id"),
    )
    op.create_index(
        "ix_conversation_repositories_conversation_id",
        "conversation_repositories",
        ["conversation_id"],
    )
    op.create_index(
        "ix_conversation_repositories_status", "conversation_repositories", ["status"]
    )

    op.create_table(
        "agent_worktrees",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("repository_id", sa.String(length=36), nullable=False),
        sa.Column("conversation_id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("branch", sa.Text(), nullable=False),
        sa.Column("base_commit", sa.String(length=64), nullable=False),
        sa.Column("head_commit", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("dirty", sa.Boolean(), nullable=False),
        sa.Column("merge_status", sa.String(length=24), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["repository_id"], ["conversation_repositories.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "agent_id", name="uq_agent_worktree_member"),
    )
    op.create_index("ix_agent_worktrees_agent_id", "agent_worktrees", ["agent_id"])
    op.create_index(
        "ix_agent_worktrees_conversation_id", "agent_worktrees", ["conversation_id"]
    )
    op.create_index("ix_agent_worktrees_repository_id", "agent_worktrees", ["repository_id"])
    op.create_index("ix_agent_worktrees_status", "agent_worktrees", ["status"])


def downgrade() -> None:
    op.drop_index("ix_agent_worktrees_status", table_name="agent_worktrees")
    op.drop_index("ix_agent_worktrees_repository_id", table_name="agent_worktrees")
    op.drop_index("ix_agent_worktrees_conversation_id", table_name="agent_worktrees")
    op.drop_index("ix_agent_worktrees_agent_id", table_name="agent_worktrees")
    op.drop_table("agent_worktrees")
    op.drop_index("ix_conversation_repositories_status", table_name="conversation_repositories")
    op.drop_index(
        "ix_conversation_repositories_conversation_id", table_name="conversation_repositories"
    )
    op.drop_table("conversation_repositories")
