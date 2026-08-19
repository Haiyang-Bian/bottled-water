from __future__ import annotations

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, uuid_str
from ..types import EncryptedText


class ConversationRepository(Base, TimestampMixin):
    __tablename__ = "conversation_repositories"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, index=True
    )
    repository_path: Mapped[str] = mapped_column(EncryptedText)
    git_common_dir: Mapped[str] = mapped_column(EncryptedText)
    base_commit: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)


class AgentWorktree(Base, TimestampMixin):
    __tablename__ = "agent_worktrees"
    __table_args__ = (
        UniqueConstraint("conversation_id", "agent_id", name="uq_agent_worktree_member"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    repository_id: Mapped[str] = mapped_column(
        ForeignKey("conversation_repositories.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.id", ondelete="CASCADE"), index=True)
    path: Mapped[str] = mapped_column(EncryptedText)
    branch: Mapped[str] = mapped_column(EncryptedText)
    base_commit: Mapped[str] = mapped_column(String(64))
    head_commit: Mapped[str] = mapped_column(String(64))
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(24), default="ready", index=True)
    dirty: Mapped[bool] = mapped_column(Boolean, default=False)
    merge_status: Mapped[str] = mapped_column(String(24), default="idle")
    last_error: Mapped[str] = mapped_column(EncryptedText, default="")
