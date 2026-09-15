"""Atomic completion and bounded continuation ports shared by hosts and Kernel."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from agent_runtime.core.run_types import ContextDelta, ContextSnapshot, EventEnvelope, RunResult


@dataclass(frozen=True)
class ContinuationRun:
    run_id: str
    scope_id: str
    request: str
    state: str
    reason_code: str | None
    sequence: int
    history_complete: bool = True


class ContinuationReader(Protocol):
    async def read(self, snapshot: ContextSnapshot) -> dict: ...


class RunCompletionPort(Protocol):
    async def try_complete(
        self, delta: ContextDelta, result: RunResult, terminal_event: EventEnvelope
    ) -> ContextSnapshot | None: ...
