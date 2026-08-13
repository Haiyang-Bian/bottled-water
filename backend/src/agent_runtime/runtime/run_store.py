"""RunStore implementations for Runtime Kernel V1."""

from __future__ import annotations

import asyncio

from ..core.run_types import RunRequest, RunResult, RunSnapshot


class InMemoryRunStore:
    def __init__(self) -> None:
        self.created: dict[str, RunSnapshot] = {}
        self.finished: dict[str, RunResult] = {}
        self._lock = asyncio.Lock()

    async def create(self, request: RunRequest, snapshot: RunSnapshot) -> None:
        async with self._lock:
            if request.run_id in self.created:
                raise ValueError(f"Run already exists: {request.run_id}")
            self.created[request.run_id] = snapshot

    async def try_finish(self, result: RunResult) -> bool:
        async with self._lock:
            if result.run_id in self.finished:
                return False
            self.finished[result.run_id] = result
            return True
