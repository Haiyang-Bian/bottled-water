from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_runtime import (
    AgentConfig,
    AgentMemory,
    ContextDelta,
    RunRequest,
    RunResult,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    Usage,
)
from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import RunSnapshot
from app.persistence.runtime_store import SQLContextStore, SQLRunStore
from db.base import Base
from db.models import Conversation, RuntimeRun, User


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


async def test_sql_run_store_terminal_cas_and_process_lost_recovery(tmp_path):
    engine, factory = await _database(tmp_path)
    store = SQLRunStore(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="run",
        context_scope_id="scope",
        input="work",
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    snapshot = RunSnapshot(
        run_id="run",
        context_scope_id="scope",
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
        await store.create(request, snapshot)
        result = RunResult(
            run_id="run",
            context_scope_id="scope",
            state=RunState.COMPLETED,
            reason_code="completed",
            started_at=now,
            finished_at=now,
            usage=Usage(),
        )
        assert await store.try_finish(result) is True
        assert await store.try_finish(result) is False

        lost_request = RunRequest(
            run_id="lost",
            context_scope_id="scope",
            input="work",
            agents=request.agents,
            policy=NeverCalledPolicy(),
        )
        await store.create(lost_request, snapshot.__class__(**{**snapshot.__dict__, "run_id": "lost"}))
        assert await store.recover_process_lost("scope") == ["lost"]
        async with factory() as db:
            lost = await db.get(RuntimeRun, "lost")
            assert lost.state == "failed"
            assert lost.reason_code == "process_lost"
    finally:
        await engine.dispose()
