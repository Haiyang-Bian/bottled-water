from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, TimestampMixin, utcnow, uuid_str
from ..types import ContentJSON, EncryptedText, SensitiveJSON

if TYPE_CHECKING:
    from .agents import Agent
    from .users import User


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    creator_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    chat_type: Mapped[str] = mapped_column(String(20), default="single", index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(EncryptedText, default="")
    avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(30), default="active", index=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    last_message_preview: Mapped[str] = mapped_column(EncryptedText, default="")
    last_message_sender: Mapped[str] = mapped_column(String(120), default="")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    activity_score: Mapped[int] = mapped_column(Integer, default=0)
    message_count: Mapped[int] = mapped_column(Integer, default=0)
    extra: Mapped[dict] = mapped_column("metadata", ContentJSON, default=dict)
    generation_status: Mapped[str] = mapped_column(String(20), default="idle")
    active_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    creator: Mapped["User"] = relationship(back_populates="conversations")
    participants: Mapped[list["ConversationParticipant"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )
    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class ConversationParticipant(Base, TimestampMixin):
    __tablename__ = "conversation_participants"
    __table_args__ = (UniqueConstraint("conversation_id", "agent_id", name="uq_conv_agent"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    participant_type: Mapped[str] = mapped_column(String(20))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(ForeignKey("agents.id"), nullable=True)
    role: Mapped[str] = mapped_column(String(30), default="member")
    nickname: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unread_count: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    conversation: Mapped["Conversation"] = relationship(back_populates="participants")
    agent: Mapped["Agent | None"] = relationship()


class Message(Base, TimestampMixin):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    client_message_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    conversation_id: Mapped[str] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    sender_type: Mapped[str] = mapped_column(String(20), index=True)
    sender_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    sender_name: Mapped[str] = mapped_column(String(120), default="")
    sender_avatar_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_type: Mapped[str] = mapped_column(String(40), default="text", index=True)
    content: Mapped[dict] = mapped_column(ContentJSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="sent", index=True)
    reply_to_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    version_count: Mapped[int] = mapped_column(Integer, default=1)
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    extra: Mapped[dict] = mapped_column("metadata", SensitiveJSON, default=dict)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")


class MessageVersion(Base):
    __tablename__ = "message_versions"
    __table_args__ = (UniqueConstraint("message_id", "version", name="uq_message_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid_str)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    content: Mapped[dict] = mapped_column(ContentJSON, default=dict)
    edit_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    edited_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
