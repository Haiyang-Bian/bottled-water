"""disable legacy demo data

Revision ID: c7a8b9d0e1f2
Revises: 9f1c2d3e4a5b
Create Date: 2026-08-12 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c7a8b9d0e1f2"
down_revision: Union[str, None] = "9f1c2d3e4a5b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


DEMO_EMAIL = "demo@agenthub.local"
DEMO_USERNAME = "demo"
BUILTIN_AGENT_NAMES = (
    "Master Agent",
    "Frontend Worker",
    "Backend Worker",
    "Reviewer",
    "Deploy Agent",
    "Writing Agent",
    "Daily Chat Agent",
)


def upgrade() -> None:
    connection = op.get_bind()
    user_id = connection.execute(
        sa.text(
            "SELECT id FROM users WHERE email = :email AND username = :username LIMIT 1"
        ),
        {"email": DEMO_EMAIL, "username": DEMO_USERNAME},
    ).scalar()
    if not user_id:
        return

    connection.execute(
        sa.text(
            "UPDATE agents SET owner_id = NULL "
            "WHERE owner_id = :user_id AND name IN :names"
        ).bindparams(sa.bindparam("names", expanding=True)),
        {"user_id": user_id, "names": BUILTIN_AGENT_NAMES},
    )
    connection.execute(
        sa.text(
            "UPDATE workspaces SET status = 'deleted', deleted_at = CURRENT_TIMESTAMP "
            "WHERE owner_id = :user_id AND name = :name AND deleted_at IS NULL"
        ),
        {"user_id": user_id, "name": "默认全栈工作区"},
    )
    connection.execute(
        sa.text("DELETE FROM user_roles WHERE user_id = :user_id"),
        {"user_id": user_id},
    )
    connection.execute(
        sa.text(
            "UPDATE users SET status = 'disabled', role = 'member' WHERE id = :user_id"
        ),
        {"user_id": user_id},
    )


def downgrade() -> None:
    # The migration intentionally does not recreate demo credentials or grant privileges.
    pass
