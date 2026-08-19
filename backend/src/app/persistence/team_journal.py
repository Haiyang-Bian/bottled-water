"""SQL adapter for atomic team messages and Runtime collaboration events."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC

from sqlalchemy import select

from agent_runtime.core.run_types import EventEnvelope, TeamMessage, TeamMessagePage
from agent_runtime.runtime.run_journal import sanitize_event_for_persistence
from agent_runtime.runtime.team_collaboration import CollaborationProtocolError
from db.models import ConversationTeamSettings, RuntimeEvent, RuntimeTeamMessage
from db.session import AsyncSessionLocal

from .runtime_journal import SQLRunJournal, _event_from_row


class SQLTeamJournal:
    def __init__(self, session_factory=None) -> None:
        self._session_factory = session_factory or AsyncSessionLocal

    async def append_message(
        self, message: TeamMessage, event: EventEnvelope
    ) -> tuple[TeamMessage, EventEnvelope]:
        async with self._session_factory() as db:
            async with db.begin():
                existing = await db.get(RuntimeTeamMessage, message.message_id)
                if existing is not None:
                    persisted_event = await db.get(RuntimeEvent, event.event_id)
                    if persisted_event is None:
                        raise CollaborationProtocolError("Team message exists without its Runtime event")
                    return _message_from_row(existing), _event_from_row(persisted_event)

                run = await SQLRunJournal._locked_run(db, message.run_id)
                settings = await self._locked_settings(db, message.context_scope_id)
                persisted = replace(message, sequence=settings.last_message_sequence + 1)
                persisted_event = sanitize_event_for_persistence(
                    replace(
                        event,
                        payload={
                            **event.payload,
                            "message_id": persisted.message_id,
                            "team_sequence": persisted.sequence,
                        },
                    )
                )
                await SQLRunJournal._append_locked(db, run, persisted_event)
                db.add(_message_row(persisted))
                settings.last_message_sequence = persisted.sequence
                await db.flush()
                return persisted, persisted_event

    async def mark_consumed(
        self, message_id: str, agent_id: str, event: EventEnvelope
    ) -> tuple[TeamMessage, EventEnvelope]:
        async with self._session_factory() as db:
            async with db.begin():
                row = await db.scalar(
                    select(RuntimeTeamMessage)
                    .where(RuntimeTeamMessage.id == message_id)
                    .with_for_update()
                )
                if row is None:
                    raise CollaborationProtocolError(f"Unknown team message: {message_id}")
                consumed = list(dict.fromkeys([*(row.consumed_by or []), agent_id]))
                expected = set(row.recipient_agent_ids or [])
                if not expected or expected <= set(consumed):
                    row.status = "consumed"
                row.consumed_by = consumed
                run = await SQLRunJournal._locked_run(db, row.run_id)
                persisted_event = sanitize_event_for_persistence(
                    replace(
                        event,
                        payload={**event.payload, "message_id": message_id, "agent_id": agent_id},
                    )
                )
                await SQLRunJournal._append_locked(db, run, persisted_event)
                await db.flush()
                return _message_from_row(row), persisted_event

    async def resolve_thread(
        self, thread_id: str, resolved_by: str, event: EventEnvelope
    ) -> EventEnvelope:
        async with self._session_factory() as db:
            async with db.begin():
                rows = list(
                    (
                        await db.scalars(
                            select(RuntimeTeamMessage)
                            .where(
                                RuntimeTeamMessage.run_id == event.run_id,
                                RuntimeTeamMessage.thread_id == thread_id,
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                if not rows:
                    raise CollaborationProtocolError(
                        f"Unknown collaboration thread: {thread_id}"
                    )
                run = await SQLRunJournal._locked_run(db, event.run_id)
                persisted_event = sanitize_event_for_persistence(
                    replace(
                        event,
                        payload={
                            **event.payload,
                            "thread_id": thread_id,
                            "resolved_by": resolved_by,
                        },
                    )
                )
                await SQLRunJournal._append_locked(db, run, persisted_event)
                for row in rows:
                    row.status = "resolved"
                    row.resolved_at = event.occurred_at
                return persisted_event

    async def interrupt_run(self, run_id: str) -> int:
        async with self._session_factory() as db:
            async with db.begin():
                rows = list(
                    (
                        await db.scalars(
                            select(RuntimeTeamMessage)
                            .where(
                                RuntimeTeamMessage.run_id == run_id,
                                RuntimeTeamMessage.status == "pending",
                            )
                            .with_for_update()
                        )
                    ).all()
                )
                for row in rows:
                    row.status = "interrupted"
                return len(rows)

    async def read_messages(
        self, context_scope_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> TeamMessagePage:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._session_factory() as db:
            return await self.read_messages_with_session(
                db,
                context_scope_id,
                after_sequence=after_sequence,
                limit=limit,
            )

    @staticmethod
    async def read_messages_with_session(
        db,
        context_scope_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> TeamMessagePage:  # noqa: ANN001
        settings = await db.get(ConversationTeamSettings, context_scope_id)
        rows = list(
            (
                await db.scalars(
                    select(RuntimeTeamMessage)
                    .where(
                        RuntimeTeamMessage.conversation_id == context_scope_id,
                        RuntimeTeamMessage.sequence > after_sequence,
                    )
                    .order_by(RuntimeTeamMessage.sequence)
                    .limit(limit)
                )
            ).all()
        )
        items = tuple(_message_from_row(row) for row in rows)
        return TeamMessagePage(
            items=items,
            next_sequence=items[-1].sequence if items else after_sequence,
            last_sequence=settings.last_message_sequence if settings else 0,
        )

    @staticmethod
    async def _locked_settings(db, conversation_id: str) -> ConversationTeamSettings:  # noqa: ANN001
        settings = await db.scalar(
            select(ConversationTeamSettings)
            .where(ConversationTeamSettings.conversation_id == conversation_id)
            .with_for_update()
        )
        if settings is None:
            settings = ConversationTeamSettings(conversation_id=conversation_id)
            db.add(settings)
            await db.flush()
        return settings


def _message_row(message: TeamMessage) -> RuntimeTeamMessage:
    return RuntimeTeamMessage(
        id=message.message_id,
        run_id=message.run_id,
        conversation_id=message.context_scope_id,
        sequence=message.sequence,
        sender_type=message.sender_type,
        sender_id=message.sender_id,
        channel=message.channel,
        recipient_agent_ids=list(message.recipient_agent_ids),
        thread_id=message.thread_id,
        reply_to_message_id=message.reply_to_message_id,
        content=message.content,
        expects_reply=message.expects_reply,
        status=message.status,
        consumed_by=list(message.consumed_by),
        created_at=message.created_at,
        resolved_at=message.resolved_at,
    )


def _message_from_row(row: RuntimeTeamMessage) -> TeamMessage:
    return TeamMessage(
        message_id=row.id,
        run_id=row.run_id,
        context_scope_id=row.conversation_id,
        sequence=row.sequence,
        sender_type=row.sender_type,
        sender_id=row.sender_id,
        channel=row.channel,
        recipient_agent_ids=tuple(row.recipient_agent_ids or []),
        thread_id=row.thread_id,
        reply_to_message_id=row.reply_to_message_id,
        content=row.content,
        expects_reply=row.expects_reply,
        status=row.status,
        consumed_by=tuple(row.consumed_by or []),
        created_at=(row.created_at.replace(tzinfo=UTC) if row.created_at.tzinfo is None else row.created_at),
        resolved_at=(
            row.resolved_at.replace(tzinfo=UTC)
            if row.resolved_at is not None and row.resolved_at.tzinfo is None
            else row.resolved_at
        ),
    )
