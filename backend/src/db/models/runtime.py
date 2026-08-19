from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, TimestampMixin, utcnow
from ..types import ContentJSON, EncryptedText, SensitiveJSON


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
    last_event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    journal_version: Mapped[int | None] = mapped_column(Integer, nullable=True, default=1)
    output: Mapped[str] = mapped_column(EncryptedText, default="")
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


class RuntimeEvent(Base):
    __tablename__ = "runtime_events"
    __table_args__ = (
        UniqueConstraint("run_id", "sequence", name="uq_runtime_events_run_sequence"),
    )

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_runs.id", ondelete="CASCADE"), index=True
    )
    context_scope_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(120), index=True)
    source: Mapped[str] = mapped_column(String(120), default="kernel")
    target: Mapped[str | None] = mapped_column(String(120), nullable=True)
    payload: Mapped[dict] = mapped_column(ContentJSON, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    causation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    persisted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RuntimeEventConsumer(Base, TimestampMixin):
    __tablename__ = "runtime_event_consumers"

    consumer_name: Mapped[str] = mapped_column(String(120), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_runs.id", ondelete="CASCADE"), primary_key=True
    )
    last_sequence: Mapped[int] = mapped_column(Integer, default=0)
    last_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True)


class ConversationTeamSettings(Base, TimestampMixin):
    __tablename__ = "conversation_team_settings"

    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), primary_key=True
    )
    summary_agent_id: Mapped[str | None] = mapped_column(
        ForeignKey("agents.id", ondelete="SET NULL"), nullable=True
    )
    live_user_input: Mapped[bool] = mapped_column(Boolean, default=True)
    max_messages: Mapped[int] = mapped_column(Integer, default=64)
    max_agent_turns: Mapped[int] = mapped_column(Integer, default=12)
    max_open_threads: Mapped[int] = mapped_column(Integer, default=24)
    max_message_chars: Mapped[int] = mapped_column(Integer, default=8_000)
    last_message_sequence: Mapped[int] = mapped_column(Integer, default=0)


class RuntimeTeamMessage(Base):
    __tablename__ = "runtime_team_messages"
    __table_args__ = (
        UniqueConstraint(
            "conversation_id", "sequence", name="uq_runtime_team_messages_conversation_sequence"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        ForeignKey("runtime_runs.id", ondelete="CASCADE"), index=True
    )
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    sender_type: Mapped[str] = mapped_column(String(20))
    sender_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(20))
    recipient_agent_ids: Mapped[list] = mapped_column(ContentJSON, default=list)
    thread_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    content: Mapped[str] = mapped_column(EncryptedText)
    expects_reply: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    consumed_by: Mapped[list] = mapped_column(ContentJSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
