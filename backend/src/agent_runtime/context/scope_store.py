"""ContextScope stores and run-local versioned Blackboard state."""

from __future__ import annotations

import asyncio
from copy import deepcopy

from ..core.ports import ContextConflictError
from ..core.run_types import AgentMemory, ContextDelta, ContextSnapshot


class VersionedBlackboard:
    def __init__(self, value: dict | None = None, *, version: int = 0) -> None:
        self._value = deepcopy(value or {})
        self._version = version
        self._lock = asyncio.Lock()

    @property
    def version(self) -> int:
        return self._version

    async def read(self) -> tuple[int, dict]:
        async with self._lock:
            return self._version, deepcopy(self._value)

    async def update(self, expected_version: int, patch: dict) -> tuple[int, dict]:
        async with self._lock:
            if expected_version != self._version:
                raise ContextConflictError(
                    f"Blackboard version conflict: expected {expected_version}, actual {self._version}"
                )
            self._value.update(deepcopy(patch))
            self._version += 1
            return self._version, deepcopy(self._value)


class InMemoryContextStore:
    def __init__(self) -> None:
        self._snapshots: dict[str, ContextSnapshot] = {}
        self._lock = asyncio.Lock()

    async def load(self, scope_id: str) -> ContextSnapshot:
        async with self._lock:
            value = self._snapshots.get(scope_id)
            if value is None:
                value = ContextSnapshot(scope_id=scope_id)
                self._snapshots[scope_id] = value
            return _clone_snapshot(value)

    async def commit(self, scope_id: str, delta: ContextDelta) -> ContextSnapshot:
        async with self._lock:
            current = self._snapshots.get(scope_id) or ContextSnapshot(scope_id=scope_id)
            if current.version != delta.expected_version:
                raise ContextConflictError(
                    f"Context version conflict: expected {delta.expected_version}, actual {current.version}"
                )
            memories: dict[str, AgentMemory] = dict(current.agent_memories)
            memories.update(delta.agent_memories)
            updated = ContextSnapshot(
                scope_id=scope_id,
                version=current.version + 1,
                messages=tuple(deepcopy(delta.messages)),
                blackboard=deepcopy(delta.blackboard),
                agent_memories=memories,
            )
            self._snapshots[scope_id] = updated
            return _clone_snapshot(updated)


def _clone_snapshot(value: ContextSnapshot) -> ContextSnapshot:
    return ContextSnapshot(
        scope_id=value.scope_id,
        version=value.version,
        messages=tuple(deepcopy(value.messages)),
        blackboard=deepcopy(value.blackboard),
        agent_memories=dict(value.agent_memories),
    )
