"""Durable Run journal primitives and an in-memory reference implementation."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import replace
from typing import Any

from ..core.run_types import EventEnvelope, EventPage, RunRequest, RunResult, RunSnapshot


class EventJournalError(RuntimeError):
    """Base class for durable event journal failures."""


class EventSequenceConflictError(EventJournalError):
    """Raised when an event reuses an occupied Run sequence or event id."""


_PRIVATE_REASONING_KEYS = {
    "chain_of_thought",
    "reasoning",
    "reasoning_content",
    "thinking",
}
_CREDENTIAL_KEY_PARTS = (
    "access_token",
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
)
_STRUCTURAL_THINKING_KEYS = {
    "agent_id",
    "agent_name",
    "client_message_id",
    "conversation_id",
    "generation_id",
    "message_id",
    "stream_message_id",
    "thinking_enabled",
}


def sanitize_event_for_persistence(event: EventEnvelope) -> EventEnvelope:
    """Return a journal-safe copy without private reasoning or credentials."""

    if event.type in {"agent.thinking", "model.reasoning", "model.thinking"}:
        payload = {
            key: deepcopy(value)
            for key, value in event.payload.items()
            if key in _STRUCTURAL_THINKING_KEYS
        }
        payload["redacted"] = True
    else:
        payload = _sanitize_value(event.payload)
    return replace(event, payload=payload)


def _sanitize_value(value: Any, key: str = "") -> Any:
    normalized = key.strip().lower()
    if normalized in _PRIVATE_REASONING_KEYS:
        return "[redacted]"
    if any(part in normalized for part in _CREDENTIAL_KEY_PARTS):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_value(item, str(item_key)) for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    return deepcopy(value)


class InMemoryRunJournal:
    """Deterministic journal used by Runtime unit tests and embedding clients."""

    def __init__(self) -> None:
        self.requests: dict[str, RunRequest] = {}
        self.created: dict[str, RunSnapshot] = {}
        self.finished: dict[str, RunResult] = {}
        self.events: dict[str, list[EventEnvelope]] = {}
        self._event_ids: dict[str, EventEnvelope] = {}
        self.memory_jobs: dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def create_run(self, request: RunRequest, snapshot: RunSnapshot) -> None:
        async with self._lock:
            if request.run_id in self.created:
                raise ValueError(f"Run already exists: {request.run_id}")
            self.requests[request.run_id] = request
            self.created[request.run_id] = snapshot
            self.events[request.run_id] = []

    async def list_scope_runs(self, scope_id):
        from agent_contracts.persistence import ContinuationRun

        async with self._lock:
            return [
                ContinuationRun(
                    key,
                    scope_id,
                    self.requests[key].input,
                    result.state.value,
                    result.reason_code,
                    self.events[key][-1].sequence,
                )
                for key, result in self.finished.items()
                if result.context_scope_id == scope_id
            ]

    async def append_event(self, event: EventEnvelope) -> None:
        persisted = sanitize_event_for_persistence(event)
        async with self._lock:
            self._append_locked(persisted)

    async def try_finish(self, result: RunResult, terminal_event: EventEnvelope) -> bool:
        persisted = sanitize_event_for_persistence(terminal_event)
        async with self._lock:
            if result.run_id in self.finished:
                return False
            old_events, old_ids = list(self.events[result.run_id]), dict(self._event_ids)
            old_jobs = dict(self.memory_jobs)
            try:
                self._terminal_outbox(result.run_id)
                self._append_locked(persisted)
                self.finished[result.run_id] = result
            except BaseException:
                self.events[result.run_id], self._event_ids = old_events, old_ids
                self.memory_jobs = old_jobs
                self.finished.pop(result.run_id, None)
                raise
            return True

    def _terminal_outbox(self, run_id):
        if self.requests[run_id].metadata.get("memory_enabled"):
            self.memory_jobs.setdefault(run_id, "pending")

    async def read_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> EventPage:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._lock:
            if run_id not in self.created:
                raise KeyError(run_id)
            all_events = self.events[run_id]
            items = tuple(event for event in all_events if event.sequence > after_sequence)[:limit]
            next_sequence = items[-1].sequence if items else after_sequence
            last_sequence = all_events[-1].sequence if all_events else 0
            return EventPage(
                items=items,
                next_sequence=next_sequence,
                last_sequence=last_sequence,
                terminal=run_id in self.finished,
            )

    def _append_locked(self, event: EventEnvelope) -> None:
        if event.run_id not in self.created:
            raise KeyError(event.run_id)
        existing_id = self._event_ids.get(event.event_id)
        if existing_id is not None:
            if existing_id == event:
                return
            raise EventSequenceConflictError(f"Event id already exists: {event.event_id}")
        run_events = self.events[event.run_id]
        expected = run_events[-1].sequence + 1 if run_events else 1
        if event.sequence != expected:
            raise EventSequenceConflictError(
                f"Run {event.run_id} expected sequence {expected}, got {event.sequence}"
            )
        run_events.append(event)
        self._event_ids[event.event_id] = event
