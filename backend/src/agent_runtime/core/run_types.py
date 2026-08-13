"""Public types for the Runtime Kernel V1 run lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from .types import AgentConfig, AgentReport


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class RunState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in {self.COMPLETED, self.FAILED, self.CANCELLED}


@dataclass(frozen=True)
class RuntimeLimits:
    wall_time_seconds: float = 1200.0
    idle_time_seconds: float = 180.0
    max_decisions: int = 50
    max_total_tokens: int = 500_000
    max_no_progress: int = 4
    cancellation_grace_seconds: float = 5.0

    def __post_init__(self) -> None:
        values = {
            "wall_time_seconds": self.wall_time_seconds,
            "idle_time_seconds": self.idle_time_seconds,
            "max_decisions": self.max_decisions,
            "max_total_tokens": self.max_total_tokens,
            "max_no_progress": self.max_no_progress,
            "cancellation_grace_seconds": self.cancellation_grace_seconds,
        }
        invalid = [name for name, value in values.items() if value <= 0]
        if invalid:
            raise ValueError(f"Runtime limits must be positive: {', '.join(invalid)}")


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated: bool = False

    @property
    def total_tokens(self) -> int:
        return max(0, self.prompt_tokens) + max(0, self.completion_tokens)

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += max(0, other.prompt_tokens)
        self.completion_tokens += max(0, other.completion_tokens)
        self.estimated = self.estimated or other.estimated

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "estimated": self.estimated,
        }


@dataclass(frozen=True)
class AgentMemory:
    agent_id: str
    summary: str = ""
    completed_tasks: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()
    facts: tuple[str, ...] = ()
    output_refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContextSnapshot:
    scope_id: str
    version: int = 0
    messages: tuple[dict[str, Any], ...] = ()
    blackboard: dict[str, Any] = field(default_factory=dict)
    agent_memories: dict[str, AgentMemory] = field(default_factory=dict)


@dataclass(frozen=True)
class ContextDelta:
    expected_version: int
    blackboard: dict[str, Any]
    messages: tuple[dict[str, Any], ...] = ()
    agent_memories: dict[str, AgentMemory] = field(default_factory=dict)


@dataclass(frozen=True)
class EventEnvelope:
    run_id: str
    context_scope_id: str
    sequence: int
    type: str
    payload: dict[str, Any]
    source: str = "kernel"
    target: str | None = None
    correlation_id: str | None = None
    causation_id: str | None = None
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True)
class EventPage:
    """One ordered page from a Run's durable event journal."""

    items: tuple[EventEnvelope, ...]
    next_sequence: int
    last_sequence: int
    terminal: bool
    history_complete: bool = True


@dataclass(frozen=True)
class SchedulingProposal:
    action: str
    target_agent_ids: tuple[str, ...] = ()
    task: str = ""
    rationale: str = ""
    usage: Usage = field(default_factory=Usage)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PolicySnapshot:
    run_id: str
    context_scope_id: str
    input: str
    agents: tuple[AgentConfig, ...]
    context: ContextSnapshot
    reports: tuple[AgentReport, ...] = ()
    decision_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RunRequest:
    context_scope_id: str
    input: str
    agents: tuple[AgentConfig, ...]
    policy: Any
    run_id: str = field(default_factory=lambda: str(uuid4()))
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.run_id.strip():
            raise ValueError("run_id is required")
        if not self.context_scope_id.strip():
            raise ValueError("context_scope_id is required")
        if not self.agents:
            raise ValueError("at least one Agent is required")


@dataclass(frozen=True)
class AgentExecutionRequest:
    run_id: str
    context_scope_id: str
    agent: AgentConfig
    task: str
    input: str
    context: ContextSnapshot
    token_budget_remaining: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AgentExecutionResult:
    agent_id: str
    report: AgentReport
    output: str = ""
    usage: Usage = field(default_factory=Usage)
    memory: AgentMemory | None = None
    blackboard_update: dict[str, Any] = field(default_factory=dict)
    progress: bool = True


@dataclass(frozen=True)
class RunResult:
    run_id: str
    context_scope_id: str
    state: RunState
    reason_code: str
    started_at: datetime
    finished_at: datetime
    usage: Usage
    context_version: int = 0
    output: str = ""


@dataclass(frozen=True)
class RunSnapshot:
    run_id: str
    context_scope_id: str
    state: RunState
    reason_code: str | None
    sequence: int
    decision_count: int
    no_progress_count: int
    usage: Usage
    context_version: int
    limits: RuntimeLimits
    started_at: datetime | None
    finished_at: datetime | None
