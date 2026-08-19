"""Durable peer-to-peer collaboration state for one Runtime Run."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from ..core.run_types import EventEnvelope, TeamMessage, TeamMessagePage
from .run_journal import InMemoryRunJournal


class CollaborationProtocolError(RuntimeError):
    pass


class CollaborationMessageBudgetExceeded(CollaborationProtocolError):
    pass


class AgentTurnBudgetExceeded(CollaborationProtocolError):
    pass


class InMemoryTeamJournal:
    """Reference TeamJournal sharing the RunJournal transaction lock."""

    def __init__(self, run_journal: InMemoryRunJournal) -> None:
        self.run_journal = run_journal
        self.messages: dict[str, list[TeamMessage]] = defaultdict(list)
        self._by_id: dict[str, TeamMessage] = {}

    async def append_message(
        self, message: TeamMessage, event: EventEnvelope
    ) -> tuple[TeamMessage, EventEnvelope]:
        async with self.run_journal._lock:  # noqa: SLF001 - shared reference transaction
            existing = self._by_id.get(message.message_id)
            if existing is not None:
                if existing == message:
                    return existing, event
                raise CollaborationProtocolError(f"Team message id already exists: {message.message_id}")
            items = self.messages[message.context_scope_id]
            persisted = replace(message, sequence=(items[-1].sequence + 1 if items else 1))
            persisted_event = replace(
                event,
                payload={
                    **event.payload,
                    "message_id": persisted.message_id,
                    "team_sequence": persisted.sequence,
                },
            )
            self.run_journal._append_locked(persisted_event)  # noqa: SLF001
            items.append(persisted)
            self._by_id[persisted.message_id] = persisted
            return persisted, persisted_event

    async def mark_consumed(
        self, message_id: str, agent_id: str, event: EventEnvelope
    ) -> tuple[TeamMessage, EventEnvelope]:
        async with self.run_journal._lock:  # noqa: SLF001
            message = self._by_id.get(message_id)
            if message is None:
                raise CollaborationProtocolError(f"Unknown team message: {message_id}")
            consumed = tuple(dict.fromkeys((*message.consumed_by, agent_id)))
            expected = set(message.recipient_agent_ids)
            status = "consumed" if not expected or expected <= set(consumed) else message.status
            updated = replace(message, consumed_by=consumed, status=status)
            persisted_event = replace(
                event,
                payload={**event.payload, "message_id": message_id, "agent_id": agent_id},
            )
            self.run_journal._append_locked(persisted_event)  # noqa: SLF001
            self._replace(updated)
            return updated, persisted_event

    async def resolve_thread(
        self, thread_id: str, resolved_by: str, event: EventEnvelope
    ) -> EventEnvelope:
        async with self.run_journal._lock:  # noqa: SLF001
            matching = [item for item in self._by_id.values() if item.thread_id == thread_id]
            if not matching:
                raise CollaborationProtocolError(f"Unknown collaboration thread: {thread_id}")
            persisted_event = replace(
                event,
                payload={**event.payload, "thread_id": thread_id, "resolved_by": resolved_by},
            )
            self.run_journal._append_locked(persisted_event)  # noqa: SLF001
            for item in matching:
                self._replace(replace(item, status="resolved", resolved_at=event.occurred_at))
            return persisted_event

    async def interrupt_run(self, run_id: str) -> int:
        changed = 0
        async with self.run_journal._lock:  # noqa: SLF001
            for item in list(self._by_id.values()):
                if item.run_id == run_id and item.status == "pending":
                    self._replace(replace(item, status="interrupted"))
                    changed += 1
        return changed

    async def read_messages(
        self, context_scope_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> TeamMessagePage:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self.run_journal._lock:  # noqa: SLF001
            all_items = self.messages.get(context_scope_id, [])
            items = tuple(item for item in all_items if item.sequence > after_sequence)[:limit]
            return TeamMessagePage(
                items=items,
                next_sequence=items[-1].sequence if items else after_sequence,
                last_sequence=all_items[-1].sequence if all_items else 0,
            )

    def _replace(self, message: TeamMessage) -> None:
        self._by_id[message.message_id] = message
        items = self.messages[message.context_scope_id]
        for index, current in enumerate(items):
            if current.message_id == message.message_id:
                items[index] = message
                return
