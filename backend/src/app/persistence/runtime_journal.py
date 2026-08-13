"""SQL adapter for the Runtime durable Run journal."""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from sqlalchemy import select

from agent_runtime.core.run_types import (
    EventEnvelope,
    EventPage,
    RunRequest,
    RunResult,
    RunSnapshot,
    RunState,
)
from agent_runtime.runtime.run_journal import (
    EventSequenceConflictError,
    sanitize_event_for_persistence,
)
from db.models import RuntimeEvent, RuntimeRun, utcnow
from db.session import AsyncSessionLocal


class SQLRunJournal:
    """Persist Run state and its ordered EventEnvelope stream in one database."""

    def __init__(self, session_factory=AsyncSessionLocal) -> None:
        self._session_factory = session_factory

    async def create_run(self, request: RunRequest, snapshot: RunSnapshot) -> None:
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
                    last_event_sequence=0,
                    journal_version=1,
                    output="",
                    extra=dict(request.metadata),
                    started_at=snapshot.started_at or utcnow(),
                )
            )
            await db.commit()

    async def append_event(self, event: EventEnvelope) -> None:
        persisted = sanitize_event_for_persistence(event)
        async with self._session_factory() as db:
            async with db.begin():
                run = await self._locked_run(db, event.run_id)
                await self._append_locked(db, run, persisted)

    async def try_finish(self, result: RunResult, terminal_event: EventEnvelope) -> bool:
        persisted = sanitize_event_for_persistence(terminal_event)
        async with self._session_factory() as db:
            async with db.begin():
                run = await self._locked_run(db, result.run_id)
                if _is_terminal(run.state):
                    return False
                await self._append_locked(db, run, persisted)
                run.state = result.state.value
                run.reason_code = result.reason_code
                run.usage = result.usage.to_dict()
                run.context_version = result.context_version
                run.output = result.output
                run.finished_at = result.finished_at
            return True

    async def read_events(
        self, run_id: str, *, after_sequence: int = 0, limit: int = 200
    ) -> EventPage:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        async with self._session_factory() as db:
            return await self.read_events_with_session(
                db,
                run_id,
                after_sequence=after_sequence,
                limit=limit,
            )

    @staticmethod
    async def read_events_with_session(
        db,
        run_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 200,
    ) -> EventPage:  # noqa: ANN001
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        if limit <= 0:
            raise ValueError("limit must be positive")
        run = await db.get(RuntimeRun, run_id)
        if run is None:
            raise KeyError(run_id)
        rows = list(
            (
                await db.scalars(
                    select(RuntimeEvent)
                    .where(
                        RuntimeEvent.run_id == run_id,
                        RuntimeEvent.sequence > after_sequence,
                    )
                    .order_by(RuntimeEvent.sequence)
                    .limit(limit)
                )
            ).all()
        )
        items = tuple(_event_from_row(row) for row in rows)
        return EventPage(
            items=items,
            next_sequence=items[-1].sequence if items else after_sequence,
            last_sequence=run.last_event_sequence,
            terminal=_is_terminal(run.state),
            history_complete=run.journal_version is not None,
        )

    async def recover_process_lost(self, context_scope_id: str | None = None) -> list[str]:
        return await self._finish_abandoned("failed", "process_lost", context_scope_id)

    async def cancel_abandoned(self, context_scope_id: str) -> list[str]:
        return await self._finish_abandoned("cancelled", "user_cancelled", context_scope_id)

    async def _finish_abandoned(
        self, state: str, reason_code: str, context_scope_id: str | None
    ) -> list[str]:
        recovered: list[str] = []
        async with self._session_factory() as db:
            async with db.begin():
                query = (
                    select(RuntimeRun)
                    .where(RuntimeRun.state.in_(("created", "running", "cancelling")))
                    .with_for_update()
                )
                if context_scope_id is not None:
                    query = query.where(RuntimeRun.context_scope_id == context_scope_id)
                rows = list((await db.scalars(query)).all())
                now = utcnow()
                for run in rows:
                    sequence = run.last_event_sequence + 1
                    terminal = EventEnvelope(
                        run_id=run.id,
                        context_scope_id=run.context_scope_id,
                        sequence=sequence,
                        type=(
                            "system.run_cancelled" if state == "cancelled" else "system.run_failed"
                        ),
                        source="recovery",
                        payload={"state": state, "reason_code": reason_code, "usage": run.usage or {}},
                        occurred_at=now,
                    )
                    await self._append_locked(db, run, terminal)
                    run.state = state
                    run.reason_code = reason_code
                    run.finished_at = now
                    recovered.append(run.id)
        return recovered

    @staticmethod
    async def _locked_run(db, run_id: str) -> RuntimeRun:  # noqa: ANN001
        run = await db.scalar(select(RuntimeRun).where(RuntimeRun.id == run_id).with_for_update())
        if run is None:
            raise KeyError(run_id)
        return run

    @staticmethod
    async def _append_locked(db, run: RuntimeRun, event: EventEnvelope) -> None:  # noqa: ANN001
        existing = await db.get(RuntimeEvent, event.event_id)
        if existing is not None:
            if _event_matches(existing, event):
                return
            raise EventSequenceConflictError(f"Event id already exists: {event.event_id}")
        expected = run.last_event_sequence + 1
        if event.sequence != expected:
            raise EventSequenceConflictError(
                f"Run {run.id} expected sequence {expected}, got {event.sequence}"
            )
        db.add(
            RuntimeEvent(
                event_id=event.event_id,
                run_id=event.run_id,
                context_scope_id=event.context_scope_id,
                sequence=event.sequence,
                type=event.type,
                source=event.source,
                target=event.target,
                payload=dict(event.payload),
                correlation_id=event.correlation_id,
                causation_id=event.causation_id,
                occurred_at=event.occurred_at,
            )
        )
        run.last_event_sequence = event.sequence
        await db.flush()


def _event_from_row(row: RuntimeEvent) -> EventEnvelope:
    return EventEnvelope(
        event_id=row.event_id,
        run_id=row.run_id,
        context_scope_id=row.context_scope_id,
        sequence=row.sequence,
        type=row.type,
        source=row.source,
        target=row.target,
        payload=dict(row.payload or {}),
        correlation_id=row.correlation_id,
        causation_id=row.causation_id,
        occurred_at=row.occurred_at,
    )


def _event_matches(row: RuntimeEvent, event: EventEnvelope) -> bool:
    persisted = _event_from_row(row)
    return (
        persisted.event_id == event.event_id
        and persisted.run_id == event.run_id
        and persisted.context_scope_id == event.context_scope_id
        and persisted.sequence == event.sequence
        and persisted.type == event.type
        and persisted.source == event.source
        and persisted.target == event.target
        and persisted.payload == event.payload
        and persisted.correlation_id == event.correlation_id
        and persisted.causation_id == event.causation_id
        and _normalized_time(persisted.occurred_at) == _normalized_time(event.occurred_at)
    )


def _normalized_time(value: datetime) -> str:
    return value.replace(tzinfo=None).isoformat(timespec="microseconds")


def _is_terminal(state: str) -> bool:
    return state in {RunState.COMPLETED.value, RunState.FAILED.value, RunState.CANCELLED.value}
