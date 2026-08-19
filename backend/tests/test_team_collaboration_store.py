from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_runtime import (
    AgentConfig,
    EventEnvelope,
    RunRequest,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    TeamMessage,
    Usage,
)
from app.persistence.runtime_journal import SQLRunJournal
from app.persistence.team_journal import SQLTeamJournal
from db.base import Base
from db.models import Conversation, RuntimeRun, RuntimeTeamMessage, User


pytestmark = [pytest.mark.integration, pytest.mark.collaboration]


class NeverCalledPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="complete")


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'collaboration.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all(
            [
                User(
                    id="user",
                    email="team@example.com",
                    username="team",
                    password_hash="x",
                ),
                Conversation(
                    id="conversation",
                    creator_id="user",
                    chat_type="group",
                    title="Team",
                    extra={},
                ),
            ]
        )
        await db.commit()
    return engine, factory


async def test_sql_team_journal_commits_message_and_event_in_one_transaction(tmp_path):
    engine, factory = await _database(tmp_path)
    run_journal = SQLRunJournal(factory)
    team_journal = SQLTeamJournal(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="run",
        context_scope_id="conversation",
        input="work",
        agents=(AgentConfig(id="author", name="Author", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    try:
        await run_journal.create_run(
            request,
            RunSnapshot(
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
            ),
        )
        persisted, persisted_event = await team_journal.append_message(
            TeamMessage(
                message_id="team-message",
                run_id=request.run_id,
                context_scope_id=request.context_scope_id,
                sender_type="agent",
                sender_id="author",
                recipient_agent_ids=("reviewer",),
                channel="direct",
                content="Review the public contract.",
                expects_reply=True,
            ),
            EventEnvelope(
                event_id="team-event",
                run_id=request.run_id,
                context_scope_id=request.context_scope_id,
                sequence=1,
                type="collaboration.message_created",
                payload={"sender_id": "author"},
            ),
        )

        assert persisted.sequence == 1
        assert persisted_event.payload["team_sequence"] == 1
        assert (await team_journal.read_messages("conversation")).items == (persisted,)
        stored_events = (await run_journal.read_events("run")).items
        assert [item.event_id for item in stored_events] == [persisted_event.event_id]
        assert stored_events[0].payload == persisted_event.payload
        async with factory() as db:
            run = await db.get(RuntimeRun, "run")
            message = await db.get(RuntimeTeamMessage, "team-message")
            assert run.last_event_sequence == 1
            assert message.content == "Review the public contract."
    finally:
        await engine.dispose()


async def test_sql_team_journal_marks_pending_messages_interrupted(tmp_path):
    engine, factory = await _database(tmp_path)
    run_journal = SQLRunJournal(factory)
    team_journal = SQLTeamJournal(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="lost-run",
        context_scope_id="conversation",
        input="work",
        agents=(AgentConfig(id="author", name="Author", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    try:
        await run_journal.create_run(
            request,
            RunSnapshot(
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
            ),
        )
        await team_journal.append_message(
            TeamMessage(
                message_id="pending-message",
                run_id=request.run_id,
                context_scope_id=request.context_scope_id,
                sender_type="agent",
                sender_id="author",
                recipient_agent_ids=("reviewer",),
                channel="direct",
                content="Are you there?",
            ),
            EventEnvelope(
                run_id=request.run_id,
                context_scope_id=request.context_scope_id,
                sequence=1,
                type="collaboration.message_created",
                payload={},
            ),
        )
        assert await team_journal.interrupt_run(request.run_id) == 1
        assert (await team_journal.read_messages("conversation")).items[0].status == "interrupted"
    finally:
        await engine.dispose()
