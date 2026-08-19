from __future__ import annotations

from datetime import UTC, datetime
import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_runtime import (
    AgentConfig,
    AgentReport,
    AgentState,
    AgentWill,
    CollaborativeTeamPolicy,
    EventEnvelope,
    RunRequest,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    RuntimeEngine,
    SchedulingProposal,
    TeamMessage,
    Usage,
)
from app.persistence.runtime_journal import SQLRunJournal
from app.persistence.team_journal import SQLTeamJournal
from app.api.runtime_events import list_team_messages
from app.api.conversations import _create, _patch
from app.core.errors import NotFoundError
from app.services.conversation_run_manager import ConversationRunManager
from app.services.runtime_service import RuntimeBinding
from db.base import Base
from db.models import Agent, Conversation, ConversationTeamSettings, RuntimeRun, RuntimeTeamMessage, User
from app.services.serialization import conversation_to_dict
from agent_runtime.core.run_types import AgentExecutionResult


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


async def test_team_message_query_pages_audit_history_and_enforces_access(tmp_path):
    engine, factory = await _database(tmp_path)
    run_journal = SQLRunJournal(factory)
    team_journal = SQLTeamJournal(factory)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="query-run",
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
        for sequence in range(1, 3):
            await team_journal.append_message(
                TeamMessage(
                    message_id=f"query-message-{sequence}",
                    run_id=request.run_id,
                    context_scope_id=request.context_scope_id,
                    sender_type="agent",
                    sender_id="author",
                    content=f"message {sequence}",
                    recipient_agent_ids=("reviewer",),
                    channel="direct",
                ),
                EventEnvelope(
                    event_id=f"query-event-{sequence}",
                    run_id=request.run_id,
                    context_scope_id=request.context_scope_id,
                    sequence=sequence,
                    type="collaboration.message_created",
                    payload={},
                ),
            )
        async with factory() as db:
            owner = await db.get(User, "user")
            outsider = User(
                id="outsider",
                email="outsider-team@example.com",
                username="outsider-team",
                password_hash="x",
            )
            db.add(outsider)
            await db.commit()
            first = await list_team_messages(
                "conversation", after_sequence=0, limit=1, db=db, user=owner
            )
            second = await list_team_messages(
                "conversation", after_sequence=1, limit=10, db=db, user=owner
            )
            assert [item["sequence"] for item in first["data"]["items"]] == [1]
            assert first["data"]["last_sequence"] == 2
            assert second["data"]["items"][0]["content"] == "message 2"
            with pytest.raises(NotFoundError):
                await list_team_messages(
                    "conversation", after_sequence=0, limit=10, db=db, user=outsider
                )
    finally:
        await engine.dispose()


class LiveInputExecutor:
    def __init__(self) -> None:
        self.started_count = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.inboxes: dict[str, list[tuple[TeamMessage, ...]]] = {}

    async def execute(self, request, *, emit, cancellation, lease):
        self.inboxes.setdefault(request.agent.id, []).append(request.inbox)
        if len(self.inboxes[request.agent.id]) == 1:
            self.started_count += 1
            if self.started_count == 2:
                self.started.set()
            await self.release.wait()
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=AgentReport(
                agent_id=request.agent.id,
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
            ),
            output=f"{request.agent.id} done",
        )


async def test_run_manager_injects_user_input_into_active_collaborative_run(tmp_path):
    engine, factory = await _database(tmp_path)
    executor = LiveInputExecutor()
    runtime = RuntimeEngine(
        agent_executor=executor,
        run_journal=SQLRunJournal(factory),
        team_journal=SQLTeamJournal(factory),
    )
    binding = RuntimeBinding(
        engine=runtime,
        agents=(
            AgentConfig(id="author", name="Author", system_prompt="work"),
            AgentConfig(id="reviewer", name="Reviewer", system_prompt="work"),
        ),
        policy_factory=CollaborativeTeamPolicy,
        scheduling_strategy="collaborative",
        team_settings={"live_user_input": True},
    )
    manager = ConversationRunManager(session_factory=factory)
    manager._bindings["conversation"] = binding
    manager._session_model_config_ids["conversation"] = None
    manager._session_scheduling_strategies["conversation"] = "collaborative"
    manager._session_workflow_enabled["conversation"] = False
    try:
        with patch("app.events.WebSocketSink.emit", new=AsyncMock()):
            await manager.start_generation("conversation", "Start the review.")
            await executor.started.wait()
            run_id = manager.get_run_status("conversation")["run_id"]
            await manager.send_user_input(
                "conversation",
                "Review this new constraint.",
                user_message_id="live-user-message",
                agent_mentions=[{"agent_id": "reviewer"}],
            )
            assert manager.get_run_status("conversation")["run_id"] == run_id
            executor.release.set()
            await manager._running_tasks["conversation"]
            await asyncio.sleep(0.05)

        assert len(executor.inboxes["reviewer"]) == 2
        assert executor.inboxes["reviewer"][1][0].content == "Review this new constraint."
        async with factory() as db:
            rows = list(
                (
                    await db.scalars(
                        select(RuntimeTeamMessage).where(RuntimeTeamMessage.run_id == run_id)
                    )
                ).all()
            )
            assert len(rows) == 1
            assert rows[0].sender_type == "user"
    finally:
        await runtime.shutdown()
        await engine.dispose()


async def test_new_group_defaults_to_collaborative_and_legacy_strategy_is_explicit(tmp_path):
    engine, factory = await _database(tmp_path)
    try:
        async with factory() as db:
            owner = await db.get(User, "user")
            db.add_all(
                [
                    Agent(id="author", name="Author", type="custom", owner_id=owner.id),
                    Agent(id="reviewer", name="Reviewer", type="custom", owner_id=owner.id),
                ]
            )
            await db.commit()
            created = await _create(
                db,
                owner,
                {
                    "chat_type": "group",
                    "title": "Peer team",
                    "participant_agent_ids": ["author", "reviewer"],
                },
            )
            serialized = conversation_to_dict(created)
            assert serialized["scheduling_strategy"] == "collaborative"
            assert serialized["team_settings"]["max_collaboration_messages"] == 64
            settings = await db.get(ConversationTeamSettings, created.id)
            assert settings is not None
            assert settings.live_user_input is True

            updated = await _patch(
                db,
                owner,
                created.id,
                {"action": "runtime", "scheduling_strategy": "tech_lead"},
            )
            assert conversation_to_dict(updated)["scheduling_strategy"] == "tech_lead"
    finally:
        await engine.dispose()
