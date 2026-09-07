"""Host-neutral execution limits and activity reporting."""

import math
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class ExecutionLimits:
    max_model_turns: int | None = None
    request_timeout_seconds: float = 120.0

    def __post_init__(self):
        turns = self.max_model_turns
        if turns is not None and (type(turns) is not int or turns <= 0):
            raise ValueError("max_model_turns must be a positive integer or omitted")
        timeout = self.request_timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("request_timeout_seconds must be finite and positive")


class ExecutionObserver(Protocol):
    async def phase_started(self, phase_id: str, phase: str, deadline: float) -> None: ...
    async def phase_finished(self, phase_id: str, *, interrupted: bool = False) -> None: ...
    async def usage_reported(self, request_id: str, usage: dict, counters: dict) -> None: ...


class ExecutionStopped(RuntimeError):
    """A typed stop independent of model-written status reports."""

    def __init__(self, reason_code: str):
        self.reason_code = reason_code
        super().__init__(reason_code)
