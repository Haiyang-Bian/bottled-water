from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from agent_runtime import (
    AgentConfig,
    AgentReport,
    AgentState,
    AgentWill,
    CollaborativeTeamPolicy,
    EventEnvelope,
    RunRequest,
    RuntimeEngine,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    TeamMessage,
    Usage,
)
from agent_runtime.core.run_types import AgentExecutionResult
from agent_runtime.runtime.run_journal import InMemoryRunJournal
from agent_runtime.runtime.team_collaboration import InMemoryTeamJournal


pytestmark = [pytest.mark.unit, pytest.mark.collaboration]


async def _collect_events(handle):
    return [event async for event in handle.events()]


class NeverCalledPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="complete")


async def _journals():
    run_journal = InMemoryRunJournal()
    team_journal = InMemoryTeamJournal(run_journal)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="run",
        context_scope_id="conversation",
        input="work",
        agents=(AgentConfig(id="author", name="Author", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
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
    return run_journal, team_journal


async def test_team_message_and_runtime_event_commit_with_independent_sequences():
    run_journal, team_journal = await _journals()
    message = TeamMessage(
        message_id="message-1",
        run_id="run",
        context_scope_id="conversation",
        sender_type="agent",
        sender_id="author",
        recipient_agent_ids=("reviewer",),
        channel="direct",
        content="Please review the boundary.",
        expects_reply=True,
        thread_id="thread-1",
    )
    event = EventEnvelope(
        event_id="event-1",
        run_id="run",
        context_scope_id="conversation",
        sequence=1,
        type="collaboration.message_created",
        payload={"sender_id": "author", "recipient_agent_ids": ["reviewer"]},
    )

    persisted, persisted_event = await team_journal.append_message(message, event)

    assert persisted.sequence == 1
    assert persisted_event.payload["team_sequence"] == 1
    assert (await team_journal.read_messages("conversation")).items == (persisted,)
    assert (await run_journal.read_events("run")).items == (persisted_event,)


async def test_consumption_resolution_and_interruption_are_auditable():
    run_journal, team_journal = await _journals()
    message, _ = await team_journal.append_message(
        TeamMessage(
            message_id="message-1",
            run_id="run",
            context_scope_id="conversation",
            sender_type="agent",
            sender_id="author",
            recipient_agent_ids=("reviewer",),
            channel="direct",
            content="Please review.",
            expects_reply=True,
            thread_id="thread-1",
        ),
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=1,
            type="collaboration.message_created",
            payload={},
        ),
    )
    consumed, _ = await team_journal.mark_consumed(
        message.message_id,
        "reviewer",
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=2,
            type="collaboration.message_consumed",
            payload={},
        ),
    )
    await team_journal.resolve_thread(
        "thread-1",
        "reviewer",
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=3,
            type="collaboration.thread_resolved",
            payload={"conclusion": "Boundary is safe."},
        ),
    )

    assert consumed.status == "consumed"
    assert consumed.consumed_by == ("reviewer",)
    page = await team_journal.read_messages("conversation")
    assert page.items[0].status == "resolved"
    assert [item.sequence for item in (await run_journal.read_events("run")).items] == [1, 2, 3]
    assert await team_journal.interrupt_run("run") == 0


async def test_failed_runtime_event_append_does_not_publish_team_message():
    run_journal, team_journal = await _journals()

    def fail(_event):
        raise RuntimeError("event store unavailable")

    run_journal._append_locked = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="event store unavailable"):
        await team_journal.append_message(
            TeamMessage(
                run_id="run",
                context_scope_id="conversation",
                sender_type="agent",
                sender_id="author",
                content="not committed",
            ),
            EventEnvelope(
                run_id="run",
                context_scope_id="conversation",
                sequence=1,
                type="collaboration.message_created",
                payload={},
            ),
        )
    assert (await team_journal.read_messages("conversation")).items == ()


def _agent(agent_id: str) -> AgentConfig:
    return AgentConfig(id=agent_id, name=agent_id.title(), system_prompt="Collaborate")


def _result(request, output: str) -> AgentExecutionResult:
    return AgentExecutionResult(
        agent_id=request.agent.id,
        report=AgentReport(
            agent_id=request.agent.id,
            state=AgentState.COMPLETED,
            will=AgentWill.COMPLETE,
        ),
        output=output,
    )


class PeerDiscussionExecutor:
    def __init__(self) -> None:
        self.inboxes: dict[str, list[tuple[TeamMessage, ...]]] = {}
        self.summary_inbox: tuple[TeamMessage, ...] = ()

    async def execute(self, request, *, emit, cancellation, lease):
        self.inboxes.setdefault(request.agent.id, []).append(request.inbox)
        if request.metadata.get("collaboration_summary"):
            self.summary_inbox = request.inbox
            return _result(request, "Team summary with no unresolved issues.")
        if request.agent.id == "author" and not request.inbox:
            await request.team_messenger.send_message(
                sender_agent_id="author",
                recipient_agent_ids=("reviewer",),
                content="Please review the API boundary.",
                expects_reply=True,
            )
        elif request.agent.id == "reviewer" and request.inbox:
            incoming = request.inbox[0]
            await request.team_messenger.send_message(
                sender_agent_id="reviewer",
                recipient_agent_ids=("author",),
                content="The boundary is sound after one rename.",
                reply_to_message_id=incoming.message_id,
            )
        elif request.agent.id == "author" and request.inbox:
            incoming = request.inbox[0]
            await request.team_messenger.resolve_thread(
                agent_id="author",
                thread_id=str(incoming.thread_id),
                conclusion="Rename accepted.",
            )
        return _result(request, f"{request.agent.id} turn complete")


async def test_collaborative_policy_routes_private_multiround_discussion_and_one_summary():
    executor = PeerDiscussionExecutor()
    engine = RuntimeEngine(agent_executor=executor)
    handle = await engine.start(
        RunRequest(
            run_id="team-run",
            context_scope_id="team-conversation",
            input="Design the boundary together.",
            agents=(_agent("author"), _agent("reviewer"), _agent("observer")),
            policy=CollaborativeTeamPolicy(),
            metadata={
                "collaboration_enabled": True,
                "summary_agent_id": "reviewer",
            },
        )
    )

    events_task = asyncio.create_task(_collect_events(handle))
    result = await handle.result()
    events = await events_task

    assert result.state is RunState.COMPLETED
    assert result.output == "Team summary with no unresolved issues."
    assert len(executor.inboxes["author"]) == 2
    assert len(executor.inboxes["reviewer"]) == 3
    assert len(executor.inboxes["observer"]) == 1
    assert all(not inbox for inbox in executor.inboxes["observer"])
    assert {message.sender_id for message in executor.summary_inbox} == {"author", "reviewer"}
    assert sum(event.type == "collaboration.thread_resolved" for event in events) == 1
    assert sum(
        event.type == "scheduler.proposal"
        and event.payload.get("target_agent_ids") == ["reviewer"]
        and "final synthesis" in event.payload.get("rationale", "")
        for event in events
    ) == 1


class SafeCheckpointExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.inboxes: dict[str, list[tuple[TeamMessage, ...]]] = {}

    async def execute(self, request, *, emit, cancellation, lease):
        self.inboxes.setdefault(request.agent.id, []).append(request.inbox)
        if len(self.inboxes[request.agent.id]) == 1:
            self.started.set()
            await self.release.wait()
        return _result(request, f"{request.agent.id} done")


async def test_user_message_is_injected_after_active_model_checkpoint_without_second_run():
    executor = SafeCheckpointExecutor()
    engine = RuntimeEngine(agent_executor=executor)
    handle = await engine.start(
        RunRequest(
            run_id="live-input-run",
            context_scope_id="team-conversation",
            input="Start.",
            agents=(_agent("author"), _agent("reviewer")),
            policy=CollaborativeTeamPolicy(),
            metadata={"collaboration_enabled": True},
        )
    )
    await executor.started.wait()

    posted = await handle.post_message("Check this new constraint.", target_agent_ids=("reviewer",))
    assert engine.active_run_count == 1
    assert len(executor.inboxes["reviewer"]) == 1
    executor.release.set()
    result = await handle.result()

    assert result.state is RunState.COMPLETED
    assert posted.sender_type == "user"
    assert len(executor.inboxes["reviewer"]) == 2
    assert executor.inboxes["reviewer"][1][0].content == "Check this new constraint."


class SendsPastBudgetExecutor:
    async def execute(self, request, *, emit, cancellation, lease):
        if request.agent.id == "author":
            await request.team_messenger.send_message(
                sender_agent_id="author",
                recipient_agent_ids=("reviewer",),
                content="one",
            )
            await request.team_messenger.send_message(
                sender_agent_id="author",
                recipient_agent_ids=("reviewer",),
                content="two",
            )
        return _result(request, "done")


async def test_agent_message_budget_has_stable_failure_reason():
    engine = RuntimeEngine(
        agent_executor=SendsPastBudgetExecutor(),
        limits=RuntimeLimits(max_collaboration_messages=1),
    )
    handle = await engine.start(
        RunRequest(
            run_id="message-budget-run",
            context_scope_id="team-conversation",
            input="Discuss.",
            agents=(_agent("author"), _agent("reviewer")),
            policy=CollaborativeTeamPolicy(),
            metadata={"collaboration_enabled": True},
        )
    )

    result = await handle.result()

    assert result.state is RunState.FAILED
    assert result.reason_code == "collaboration_message_budget_exhausted"
