"""Atomic runtime continuation metadata.

Revision ID: b8c9d0e1f2a3
Revises: f6a7b8c9d0e1
"""

from alembic import op
import sqlalchemy as sa

revision = "b8c9d0e1f2a3"
down_revision = "f6a7b8c9d0e1"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("runtime_context_states", sa.Column("continuation", sa.JSON(), nullable=True))


def downgrade():
    op.drop_column("runtime_context_states", "continuation")
