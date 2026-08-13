from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_runtime import (
    AgentConfig,
    AgentMemory,
    ContextDelta,
    EventEnvelope,
    RunRequest,
    RunResult,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    Usage,
)
from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import RunSnapshot
from app.persistence.runtime_journal import SQLRunJournal
from app.persistence.runtime_store import SQLContextStore
from app.api.runtime_events import list_runtime_events
from app.core.errors import NotFoundError
from db.base import Base
from db.models import Conversation, RuntimeEvent, RuntimeRun, User


pytestmark = [pytest.mark.integration, pytest.mark.runtime]


class NeverCalledPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="complete")


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(
            id="user",
            email="runtime@example.com",
            username="runtime",
            password_hash="x",
        )
        db.add_all(
            [
                user,
                Conversation(
                    id="scope",
                    creator_id=user.id,
                    chat_type="single",
                    title="Runtime",
                    extra={},
                ),
            ]
        )
        await db.commit()
    return engine, factory


async def test_sql_context_store_commits_structured_state_and_rejects_conflicts(tmp_path):
    engine, factory = await _database(tmp_path)
    store = SQLContextStore(factory)
    try:
        committed = await store.commit(
            "scope",
            ContextDelta(
                expected_version=0,
                messages=({"role": "user", "content": "hello"},),
                blackboard={"fact": "value"},
                agent_memories={"agent": AgentMemory(agent_id="agent", summary="done")},
            ),
        )
        assert committed.version == 1
        assert committed.blackboard == {"fact": "value"}
        assert committed.agent_memories["agent"].summary == "done"
        assert "reasoning" not in repr(committed)
        with pytest.raises(ContextConflictError):
            await store.commit("scope", ContextDelta(expected_version=0, blackboard={}))
    finally:
        await engine.dispose()


async def test_sql_run_journal_persists_ordered_redacted_events_and_atomic_terminal(tmp_path):
    engine, factory = await _database(tmp_path)
    journal = SQLRunJournal(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="journal-run",
        context_scope_id="scope",
        input="work",
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    snapshot = RunSnapshot(
        run_id=request.run_id,
        context_scope_id=request.context_scope_id,
        state=RunState.RUNNING,
        reason_code=None,
        sequence=0,
        decision_count=0,
        no_progress_count=0,
        usage=Usage(),
        context_version=0,
        limits=RuntimeLimits(),
        started_at=now,
        finished_at=None,
    )
    try:
        await journal.create_run(request, snapshot)
        token = EventEnvelope(
            event_id="journal-event-1",
            run_id=request.run_id,
            context_scope_id=request.context_scope_id,
            sequence=1,
            type="agent.token",
            payload={"token": "answer", "reasoning": "private", "api_key": "credential"},
        )
        await journal.append_event(token)
        await journal.append_event(token)
        result = RunResult(
            run_id=request.run_id,
            context_scope_id=request.context_scope_id,
            state=RunState.COMPLETED,
            reason_code="completed",
            started_at=now,
            finished_at=datetime.now(UTC),
            usage=Usage(),
            output="answer",
        )
        terminal = EventEnvelope(
            event_id="journal-event-2",
            run_id=request.run_id,
            context_scope_id=request.context_scope_id,
            sequence=2,
            type="system.run_completed",
            payload={"state": "completed"},
        )
        assert await journal.try_finish(result, terminal) is True
        assert await journal.try_finish(result, terminal) is False

        page = await journal.read_events(request.run_id, after_sequence=0, limit=10)
        assert [event.sequence for event in page.items] == [1, 2]
        assert page.items[0].payload == {
            "token": "answer",
            "reasoning": "[redacted]",
            "api_key": "[redacted]",
        }
        assert page.terminal is True
        assert page.history_complete is True
        async with factory() as db:
            run = await db.get(RuntimeRun, request.run_id)
            events = list((await db.scalars(select(RuntimeEvent))).all())
            assert run.state == "completed"
            assert run.output == "answer"
            assert run.last_event_sequence == 2
            assert len(events) == 2
    finally:
        await engine.dispose()


async def test_sql_run_journal_process_lost_recovery_appends_atomic_terminal_event(tmp_path):
    engine, factory = await _database(tmp_path)
    journal = SQLRunJournal(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="lost-run",
        context_scope_id="scope",
        input="work",
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    snapshot = RunSnapshot(
        run_id=request.run_id,
        context_scope_id=request.context_scope_id,
        state=RunState.RUNNING,
        reason_code=None,
        sequence=0,
        decision_count=0,
        no_progress_count=0,
        usage=Usage(),
        context_version=0,
        limits=RuntimeLimits(),
        started_at=now,
        finished_at=None,
    )
    try:
        await journal.create_run(request, snapshot)
        await journal.append_event(
            EventEnvelope(
                run_id=request.run_id,
                context_scope_id=request.context_scope_id,
                sequence=1,
                type="system.run_started",
                payload={},
            )
        )
        assert await journal.recover_process_lost("scope") == [request.run_id]
        assert await journal.recover_process_lost("scope") == []
        page = await journal.read_events(request.run_id)
        assert [event.type for event in page.items] == [
            "system.run_started",
            "system.run_failed",
        ]
        assert page.items[-1].payload["reason_code"] == "process_lost"
        assert page.terminal is True
    finally:
        await engine.dispose()


async def test_runtime_event_query_pages_events_and_enforces_conversation_access(tmp_path):
    engine, factory = await _database(tmp_path)
    journal = SQLRunJournal(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="api-run",
        context_scope_id="scope",
        input="work",
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    snapshot = RunSnapshot(
        run_id=request.run_id,
        context_scope_id=request.context_scope_id,
        state=RunState.RUNNING,
        reason_code=None,
        sequence=0,
        decision_count=0,
        no_progress_count=0,
        usage=Usage(),
        context_version=0,
        limits=RuntimeLimits(),
        started_at=now,
        finished_at=None,
    )
    try:
        await journal.create_run(request, snapshot)
        for sequence in range(1, 4):
            await journal.append_event(
                EventEnvelope(
                    event_id=f"api-event-{sequence}",
                    run_id=request.run_id,
                    context_scope_id=request.context_scope_id,
                    sequence=sequence,
                    type="agent.token",
                    payload={"token": str(sequence)},
                )
            )
        async with factory() as db:
            owner = await db.get(User, "user")
            outsider = User(
                id="outsider",
                email="outsider@example.com",
                username="outsider",
                password_hash="x",
            )
            db.add(outsider)
            await db.commit()

            first = await list_runtime_events(
                "scope",
                request.run_id,
                after_sequence=0,
                limit=2,
                db=db,
                user=owner,
            )
            assert [item["sequence"] for item in first["data"]["items"]] == [1, 2]
            assert first["data"]["next_sequence"] == 2
            assert first["data"]["last_sequence"] == 3
            assert first["data"]["terminal"] is False
            assert first["data"]["items"][0]["projected_type"] == "agent.token"
            assert first["data"]["items"][0]["projected_payload"] == {
                "token": "1",
                "generation_id": "api-run",
                "conversation_id": "scope",
                "runtime_event_id": "api-event-1",
                "runtime_sequence": 1,
                "runtime_run_id": "api-run",
                "runtime_replayed": True,
            }

            second = await list_runtime_events(
                "scope",
                request.run_id,
                after_sequence=2,
                limit=2,
                db=db,
                user=owner,
            )
            assert [item["sequence"] for item in second["data"]["items"]] == [3]
            with pytest.raises(NotFoundError):
                await list_runtime_events(
                    "scope",
                    request.run_id,
                    after_sequence=0,
                    limit=200,
                    db=db,
                    user=outsider,
                )
    finally:
        await engine.dispose()
