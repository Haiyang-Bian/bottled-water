"""SQL persistence adapters for Runtime Kernel V1."""

from __future__ import annotations

from dataclasses import asdict

from sqlalchemy import select

from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import (
    AgentMemory,
    ContextDelta,
    ContextSnapshot,
    RunRequest,
    RunResult,
    RunSnapshot,
)
from db.models import RuntimeContextState, RuntimeRun, utcnow
from db.session import AsyncSessionLocal


class SQLContextStore:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self._session_factory = session_factory

    async def load(self, scope_id: str) -> ContextSnapshot:
        async with self._session_factory() as db:
            row = await db.get(RuntimeContextState, scope_id)
            if row is None:
                return ContextSnapshot(scope_id=scope_id)
            return _context_snapshot(row)

    async def commit(self, scope_id: str, delta: ContextDelta) -> ContextSnapshot:
        async with self._session_factory() as db:
            async with db.begin():
                row = await db.scalar(
                    select(RuntimeContextState)
                    .where(RuntimeContextState.context_scope_id == scope_id)
                    .with_for_update()
                )
                if row is None:
                    if delta.expected_version != 0:
                        raise ContextConflictError(
                            f"Context version conflict: expected {delta.expected_version}, actual 0"
                        )
                    row = RuntimeContextState(context_scope_id=scope_id, version=0)
                    db.add(row)
                if row.version != delta.expected_version:
                    raise ContextConflictError(
                        f"Context version conflict: expected {delta.expected_version}, actual {row.version}"
                    )
                row.version += 1
                row.messages = list(delta.messages)
                row.blackboard = dict(delta.blackboard)
                row.agent_memories = {
                    agent_id: asdict(memory) for agent_id, memory in delta.agent_memories.items()
                }
            await db.refresh(row)
            return _context_snapshot(row)


class SQLRunStore:
    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self._session_factory = session_factory

    async def create(self, request: RunRequest, snapshot: RunSnapshot) -> None:
        async with self._session_factory() as db:
            db.add(
                RuntimeRun(
                    id=request.run_id,
                    context_scope_id=request.context_scope_id,
                    state="running",
                    input_preview=request.input[:1000],
                    limits=asdict(snapshot.limits),
                    usage=snapshot.usage.to_dict(),
                    context_version=snapshot.context_version,
                    extra=dict(request.metadata),
                    started_at=snapshot.started_at or utcnow(),
                )
            )
            await db.commit()

    async def try_finish(self, result: RunResult) -> bool:
        async with self._session_factory() as db:
            async with db.begin():
                row = await db.scalar(
                    select(RuntimeRun).where(RuntimeRun.id == result.run_id).with_for_update()
                )
                if row is None or row.state in {"completed", "failed", "cancelled"}:
                    return False
                row.state = result.state.value
                row.reason_code = result.reason_code
                row.usage = result.usage.to_dict()
                row.context_version = result.context_version
                row.finished_at = result.finished_at
            return True

    async def recover_process_lost(self, context_scope_id: str | None = None) -> list[str]:
        return await self._finish_abandoned("failed", "process_lost", context_scope_id)

    async def cancel_abandoned(self, context_scope_id: str) -> list[str]:
        return await self._finish_abandoned("cancelled", "user_cancelled", context_scope_id)

    async def _finish_abandoned(
        self, state: str, reason_code: str, context_scope_id: str | None
    ) -> list[str]:
        async with self._session_factory() as db:
            query = select(RuntimeRun).where(RuntimeRun.state.in_(("created", "running", "cancelling")))
            if context_scope_id is not None:
                query = query.where(RuntimeRun.context_scope_id == context_scope_id)
            rows = list((await db.scalars(query)).all())
            now = utcnow()
            for row in rows:
                row.state = state
                row.reason_code = reason_code
                row.finished_at = now
            if rows:
                await db.commit()
            return [row.id for row in rows]


def _context_snapshot(row: RuntimeContextState) -> ContextSnapshot:
    memories = {
        agent_id: AgentMemory(
            agent_id=agent_id,
            summary=str(value.get("summary") or ""),
            completed_tasks=tuple(value.get("completed_tasks") or ()),
            blockers=tuple(value.get("blockers") or ()),
            facts=tuple(value.get("facts") or ()),
            output_refs=tuple(value.get("output_refs") or ()),
        )
        for agent_id, value in (row.agent_memories or {}).items()
        if isinstance(value, dict)
    }
    return ContextSnapshot(
        scope_id=row.context_scope_id,
        version=row.version,
        messages=tuple(row.messages or ()),
        blackboard=dict(row.blackboard or {}),
        agent_memories=memories,
    )
