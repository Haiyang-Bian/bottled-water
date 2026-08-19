from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agent_runtime.core.ports import ExecutionRootPort
from app.core.errors import ValidationAppError
from db.models import AgentWorktree, ConversationRepository


class SQLExecutionRootPort(ExecutionRootPort):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session_factory = session_factory

    async def resolve(self, context_scope_id: str, agent_id: str) -> str | None:
        async with self.session_factory() as db:
            repository = await db.scalar(
                select(ConversationRepository).where(
                    ConversationRepository.conversation_id == context_scope_id,
                    ConversationRepository.deleted_at.is_(None),
                    ConversationRepository.status == "active",
                )
            )
            if not repository:
                return None
            worktree = await db.scalar(
                select(AgentWorktree).where(
                    AgentWorktree.repository_id == repository.id,
                    AgentWorktree.agent_id == agent_id,
                    AgentWorktree.deleted_at.is_(None),
                    AgentWorktree.status == "ready",
                )
            )
            if not worktree:
                raise ValidationAppError(
                    "The Agent has no ready worktree in this bound Conversation"
                )
            try:
                path = Path(worktree.path).resolve(strict=True)
            except (OSError, RuntimeError) as exc:
                raise ValidationAppError("The assigned Agent worktree is unavailable") from exc
            if not path.is_dir():
                raise ValidationAppError("The assigned Agent worktree is unavailable")
            return str(path)
