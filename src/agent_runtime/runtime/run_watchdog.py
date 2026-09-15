"""Monotonic lifecycle budgets for a single Run."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable

from ..core.run_types import RuntimeLimits


class RunWatchdog:
    """Tracks hard deadlines and progress without treating token streams as progress."""

    def __init__(
        self,
        limits: RuntimeLimits,
        on_exceeded: Callable[[str], Awaitable[None]],
    ) -> None:
        self.limits = limits
        self._on_exceeded = on_exceeded
        self._started_at = time.monotonic()
        self._last_progress_at = self._started_at
        self._stopped = asyncio.Event()
        self._phases: dict[str, tuple[str, float]] = {}

    @property
    def deadline(self):
        return self._started_at + self.limits.wall_time_seconds

    def phase_started(self, phase_id, phase, deadline):
        self._phases[phase_id] = (phase, min(deadline, self.deadline))

    def phase_finished(self, phase_id):
        self._phases.pop(phase_id, None)
        self._last_progress_at = time.monotonic()

    def record_progress(self) -> None:
        self._last_progress_at = time.monotonic()

    def reason(self) -> str | None:
        now = time.monotonic()
        if now - self._started_at >= self.limits.wall_time_seconds:
            return "wall_time_exceeded"
        for phase, deadline in self._phases.values():
            # Let the phase's own timeout close its adapter before the fallback fires.
            if now >= deadline + self.limits.cancellation_grace_seconds:
                return f"{phase}_timeout"
        if not self._phases and now - self._last_progress_at >= self.limits.idle_time_seconds:
            return "idle_timeout"
        return None

    async def run(self) -> None:
        interval = max(
            0.001,
            min(0.05, self.limits.wall_time_seconds / 10, self.limits.idle_time_seconds / 10),
        )
        while not self._stopped.is_set():
            reason = self.reason()
            if reason is not None:
                await self._on_exceeded(reason)
                return
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=interval)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()

    def check_decisions(self, decision_count: int) -> str | None:
        if decision_count >= self.limits.max_decisions:
            return "decision_budget_exhausted"
        return None

    def check_tokens(self, total_tokens: int) -> str | None:
        if total_tokens >= self.limits.max_total_tokens:
            return "token_budget_exhausted"
        return None

    def check_no_progress(self, no_progress_count: int) -> str | None:
        if no_progress_count >= self.limits.max_no_progress:
            return "no_progress"
        return None
