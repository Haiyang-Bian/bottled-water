"""Dependency ports used by Runtime Kernel V1."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Protocol

from .run_types import (
    AgentExecutionRequest,
    AgentExecutionResult,
    ContextDelta,
    ContextSnapshot,
    EventEnvelope,
    EventPage,
    PolicySnapshot,
    RunRequest,
    RunResult,
    RunSnapshot,
    SchedulingProposal,
    TeamMessage,
    TeamMessagePage,
)


class ContextConflictError(RuntimeError):
    pass


class ContextStore(Protocol):
    async def load(self, scope_id: str) -> ContextSnapshot: ...

    async def commit(self, scope_id: str, delta: ContextDelta) -> ContextSnapshot: ...


class RunJournal(Protocol):
    """Durable, ordered source of truth for Run state and events."""

    async def create_run(self, request: RunRequest, snapshot: RunSnapshot) -> None: ...

    async def append_event(self, event: EventEnvelope) -> None: ...

    async def try_finish(self, result: RunResult, terminal_event: EventEnvelope) -> bool: ...

    async def read_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> EventPage: ...


class TeamJournal(Protocol):
    """Atomic storage for collaboration messages and their Runtime events."""

    async def append_message(
        self, message: TeamMessage, event: EventEnvelope
    ) -> tuple[TeamMessage, EventEnvelope]: ...

    async def mark_consumed(
        self, message_id: str, agent_id: str, event: EventEnvelope
    ) -> tuple[TeamMessage, EventEnvelope]: ...

    async def resolve_thread(
        self, thread_id: str, resolved_by: str, event: EventEnvelope
    ) -> EventEnvelope: ...

    async def interrupt_run(self, run_id: str) -> int: ...

    async def read_messages(
        self, context_scope_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> TeamMessagePage: ...


class TeamMessenger(Protocol):
    async def send_message(
        self,
        *,
        sender_agent_id: str,
        content: str,
        recipient_agent_ids: tuple[str, ...] = (),
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
        expects_reply: bool = False,
    ) -> TeamMessage: ...

    async def resolve_thread(
        self, *, agent_id: str, thread_id: str, conclusion: str
    ) -> None: ...


class SchedulerPolicy(Protocol):
    async def propose(
        self, snapshot: PolicySnapshot, trigger: EventEnvelope | None
    ) -> SchedulingProposal: ...


EmitEvent = Callable[[str, dict, str, str | None, str | None, str | None], Awaitable[None]]


class AgentExecutor(Protocol):
    async def execute(
        self,
        request: AgentExecutionRequest,
        *,
        emit: EmitEvent,
        cancellation,
        lease,
    ) -> AgentExecutionResult: ...


class RunEventSink(Protocol):
    async def emit(self, event: EventEnvelope) -> None: ...


class RunHandleProtocol(Protocol):
    def events(self, *, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]: ...

    async def result(self) -> RunResult: ...

    async def cancel(self, reason: str = "user_cancelled") -> RunResult: ...

    async def post_message(
        self, content: str, *, target_agent_ids: tuple[str, ...] = ()
    ) -> TeamMessage: ...
