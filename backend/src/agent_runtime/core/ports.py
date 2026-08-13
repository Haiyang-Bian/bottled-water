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
    RunRequest,
    RunResult,
    RunSnapshot,
    SchedulingProposal,
)


class ContextConflictError(RuntimeError):
    pass


class ContextStore(Protocol):
    async def load(self, scope_id: str) -> ContextSnapshot: ...

    async def commit(self, scope_id: str, delta: ContextDelta) -> ContextSnapshot: ...


class RunStore(Protocol):
    async def create(self, request: RunRequest, snapshot: RunSnapshot) -> None: ...

    async def try_finish(self, result: RunResult) -> bool: ...


class SchedulerPolicy(Protocol):
    async def propose(self, snapshot, trigger: EventEnvelope | None) -> SchedulingProposal: ...


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
    def events(self) -> AsyncIterator[EventEnvelope]: ...

    async def result(self) -> RunResult: ...

    async def cancel(self, reason: str = "user_cancelled") -> RunResult: ...
