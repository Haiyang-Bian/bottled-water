from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin
from ..types import ContentJSON, SensitiveJSON


class RuntimeRun(Base, TimestampMixin):
    __tablename__ = "runtime_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    context_scope_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(20), index=True)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    input_preview: Mapped[str] = mapped_column(Text, default="")
    limits: Mapped[dict] = mapped_column(SensitiveJSON, default=dict)
    usage: Mapped[dict] = mapped_column(SensitiveJSON, default=dict)
    context_version: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict] = mapped_column("metadata", SensitiveJSON, default=dict)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class RuntimeContextState(Base, TimestampMixin):
    __tablename__ = "runtime_context_states"

    context_scope_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    version: Mapped[int] = mapped_column(Integer, default=0)
    messages: Mapped[list] = mapped_column(ContentJSON, default=list)
    blackboard: Mapped[dict] = mapped_column(SensitiveJSON, default=dict)
    agent_memories: Mapped[dict] = mapped_column(SensitiveJSON, default=dict)
