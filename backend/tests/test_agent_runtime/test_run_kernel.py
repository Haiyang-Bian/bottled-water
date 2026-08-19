from __future__ import annotations

import asyncio

import pytest

from agent_runtime import (
    AgentConfig,
    AgentMemory,
    AgentReport,
    AgentState,
    AgentWill,
    ContextDelta,
    RunRequest,
    RunState,
    RuntimeEngine,
    RuntimeLimits,
    SchedulingProposal,
)
from agent_runtime.context.scope_store import InMemoryContextStore, VersionedBlackboard
from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import AgentExecutionResult
from agent_runtime.runtime.cancellation import RunLeaseRevokedError
from agent_runtime.runtime.run_journal import InMemoryRunJournal
from agent_runtime.runtime.run_journal import EventJournalError


pytestmark = [pytest.mark.unit, pytest.mark.runtime]


def _agent(agent_id: str = "worker") -> AgentConfig:
    return AgentConfig(id=agent_id, name=agent_id.title(), system_prompt="Do the assigned work")


class AssignThenCompletePolicy:
    async def propose(self, snapshot, trigger):
        if snapshot.reports:
            return SchedulingProposal(action="complete")
        return SchedulingProposal(
            action="assign", target_agent_ids=(snapshot.agents[0].id,), task=snapshot.input
        )


class SuccessfulExecutor:
    async def execute(self, request, *, emit, cancellation, lease):
        await emit("model.output", {"content": "done"}, "model", None, None, None)
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=AgentReport(
                agent_id=request.agent.id,
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
            ),
            output="done",
            memory=AgentMemory(
                agent_id=request.agent.id,
                summary="Implemented the task",
                completed_tasks=(request.task,),
                facts=("result is available",),
            ),
            blackboard_update={"result": "done"},
        )


class ThinkingExecutor(SuccessfulExecutor):
    async def execute(self, request, *, emit, cancellation, lease):
        await emit(
            "agent.thinking",
            {"agent_id": request.agent.id, "thinking": "private reasoning"},
            f"agent:{request.agent.id}",
            None,
            None,
            None,
        )
        return await super().execute(
            request,
            emit=emit,
            cancellation=cancellation,
            lease=lease,
        )


async def _collect_events(handle):
    return [event async for event in handle.events()]


class CollectingSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event):
        self.events.append(event)


class JournalCheckingSink(CollectingSink):
    def __init__(self, journal) -> None:
        super().__init__()
        self.journal = journal

    async def emit(self, event):
        page = await self.journal.read_events(event.run_id)
        assert any(item.event_id == event.event_id for item in page.items)
        await super().emit(event)


async def test_run_completes_once_and_commits_structured_context():
    context_store = InMemoryContextStore()
    run_store = InMemoryRunJournal()
    engine = RuntimeEngine(
        agent_executor=SuccessfulExecutor(),
        context_store=context_store,
        run_journal=run_store,
    )

    handle = await engine.start(
        RunRequest(
            run_id="run-1",
            context_scope_id="conversation-1",
            input="ship it",
            agents=(_agent(),),
            policy=AssignThenCompletePolicy(),
        )
    )
    events_task = asyncio.create_task(_collect_events(handle))
    result = await handle.result()
    events = await events_task

    assert result.state is RunState.COMPLETED
    assert result.output == "done"
    assert [event.sequence for event in events] == list(range(1, len(events) + 1))
    assert len({event.event_id for event in events}) == len(events)
    assert sum(event.type.startswith("system.run_") and event.type != "system.run_started" for event in events) == 1
    assert len(run_store.finished) == 1

    context = await context_store.load("conversation-1")
    assert context.version == 1
    assert context.blackboard == {"result": "done"}
    assert context.messages == (
        {"role": "user", "content": "ship it"},
        {"role": "assistant", "content": "done"},
    )
    assert context.agent_memories["worker"].summary == "Implemented the task"
    assert "reasoning" not in repr(context)
    assert any(event.type == "control.assign" and event.target == "worker" for event in events)
    assert engine.active_run_count == 0


async def test_multiple_handle_subscribers_receive_the_same_durable_stream():
    journal = InMemoryRunJournal()
    sink = JournalCheckingSink(journal)
    engine = RuntimeEngine(
        agent_executor=SuccessfulExecutor(),
        run_journal=journal,
        event_sink=sink,
    )
    handle = await engine.start(
        RunRequest(
            run_id="run-multi-reader",
            context_scope_id="conversation-multi-reader",
            input="ship it",
            agents=(_agent(),),
            policy=AssignThenCompletePolicy(),
        )
    )

    first_task = asyncio.create_task(_collect_events(handle))
    second_task = asyncio.create_task(_collect_events(handle))
    await handle.result()
    first, second = await asyncio.gather(first_task, second_task)

    assert first == second
    assert sink.events == first
    assert first[-1].type == "system.run_completed"


async def test_handle_keeps_live_thinking_ephemeral_while_journal_redacts_it():
    journal = InMemoryRunJournal()
    engine = RuntimeEngine(agent_executor=ThinkingExecutor(), run_journal=journal)
    handle = await engine.start(
        RunRequest(
            run_id="run-live-thinking",
            context_scope_id="conversation-live-thinking",
            input="ship it",
            agents=(_agent(),),
            policy=AssignThenCompletePolicy(),
        )
    )

    live_events = await _collect_events(handle)
    thinking = next(event for event in live_events if event.type == "agent.thinking")
    persisted = await journal.read_events(handle.run_id)
    persisted_thinking = next(event for event in persisted.items if event.type == "agent.thinking")

    assert thinking.payload["thinking"] == "private reasoning"
    assert persisted_thinking.payload == {"agent_id": "worker", "redacted": True}


class FailingJournal(InMemoryRunJournal):
    async def append_event(self, event):
        raise EventJournalError("database unavailable")

    async def try_finish(self, result, terminal_event):
        raise EventJournalError("database unavailable")


async def test_journal_failure_converges_locally_without_publishing_uncommitted_events():
    journal = FailingJournal()
    sink = CollectingSink()
    engine = RuntimeEngine(
        agent_executor=SuccessfulExecutor(),
        run_journal=journal,
        event_sink=sink,
    )
    handle = await engine.start(
        RunRequest(
            run_id="run-journal-failure",
            context_scope_id="conversation-journal-failure",
            input="ship it",
            agents=(_agent(),),
            policy=AssignThenCompletePolicy(),
        )
    )

    events_task = asyncio.create_task(_collect_events(handle))
    result = await handle.result()

    assert result.state is RunState.FAILED
    assert result.reason_code == "event_store_error"
    assert await events_task == []
    assert sink.events == []


class NeverCompletesExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.emit = None

    async def execute(self, request, *, emit, cancellation, lease):
        self.emit = emit
        self.started.set()
        await asyncio.Future()


async def test_repeated_cancel_converges_and_late_results_lose_write_authority():
    executor = NeverCompletesExecutor()
    run_store = InMemoryRunJournal()
    sink = CollectingSink()
    engine = RuntimeEngine(agent_executor=executor, run_journal=run_store, event_sink=sink)
    handle = await engine.start(
        RunRequest(
            run_id="run-cancel",
            context_scope_id="conversation-1",
            input="wait",
            agents=(_agent(),),
            policy=AssignThenCompletePolicy(),
        )
    )
    events_task = asyncio.create_task(_collect_events(handle))
    await executor.started.wait()

    first, second = await asyncio.gather(handle.cancel("user_cancelled"), handle.cancel())
    assert executor.emit is not None
    with pytest.raises(RunLeaseRevokedError):
        await executor.emit("tool.result", {"late": True}, "tool", None, None, None)
    events = await events_task

    assert first == second
    assert first.state is RunState.CANCELLED
    assert len(run_store.finished) == 1
    assert sum(event.type == "system.run_cancelled" for event in events) == 1
    assert sink.events[-1].type == "system.late_event_rejected"


class CompleteOnSignalPolicy:
    def __init__(self) -> None:
        self.ready = asyncio.Event()
        self.release = asyncio.Event()

    async def propose(self, snapshot, trigger):
        self.ready.set()
        await self.release.wait()
        return SchedulingProposal(action="complete")


async def test_completion_and_cancellation_race_commit_exactly_one_terminal_state():
    policy = CompleteOnSignalPolicy()
    run_store = InMemoryRunJournal()
    engine = RuntimeEngine(agent_executor=SuccessfulExecutor(), run_journal=run_store)
    handle = await engine.start(
        RunRequest(
            run_id="run-race",
            context_scope_id="conversation-race",
            input="finish",
            agents=(_agent(),),
            policy=policy,
        )
    )
    events_task = asyncio.create_task(_collect_events(handle))
    await policy.ready.wait()

    cancel_task = asyncio.create_task(handle.cancel("user_cancelled"))
    policy.release.set()
    result = await cancel_task
    events = await events_task

    terminal_events = [
        event
        for event in events
        if event.type in {"system.run_completed", "system.run_failed", "system.run_cancelled"}
    ]
    assert result.state in {RunState.COMPLETED, RunState.CANCELLED}
    assert len(terminal_events) == 1
    assert len(run_store.finished) == 1


async def test_blackboard_and_context_store_reject_stale_versions():
    blackboard = VersionedBlackboard({"count": 1})
    await blackboard.update(0, {"count": 2})
    with pytest.raises(ContextConflictError):
        await blackboard.update(0, {"count": 3})

    store = InMemoryContextStore()
    await store.commit("scope", ContextDelta(expected_version=0, blackboard={"count": 1}))
    with pytest.raises(ContextConflictError):
        await store.commit("scope", ContextDelta(expected_version=0, blackboard={"count": 2}))


def test_runtime_limits_have_safe_defaults_and_reject_zero():
    assert RuntimeLimits() == RuntimeLimits(
        wall_time_seconds=1200,
        idle_time_seconds=180,
        max_decisions=50,
        max_total_tokens=500_000,
        max_no_progress=4,
        cancellation_grace_seconds=5,
        max_collaboration_messages=64,
        max_agent_turns=12,
        max_open_threads=24,
        max_team_message_chars=8_000,
    )
    with pytest.raises(ValueError):
        RuntimeLimits(max_decisions=0)


async def test_runtime_shutdown_fails_active_run_and_forgets_it():
    executor = NeverCompletesExecutor()
    engine = RuntimeEngine(agent_executor=executor)
    handle = await engine.start(
        RunRequest(
            run_id="run-shutdown",
            context_scope_id="scope-shutdown",
            input="wait",
            agents=(_agent(),),
            policy=AssignThenCompletePolicy(),
        )
    )
    await executor.started.wait()

    results = await engine.shutdown()

    assert results == (await handle.result(),)
    assert results[0].state is RunState.FAILED
    assert results[0].reason_code == "runtime_shutdown"
    assert engine.active_run_count == 0
    with pytest.raises(RuntimeError, match="shut down"):
        await engine.start(
            RunRequest(
                run_id="run-after-shutdown",
                context_scope_id="scope-shutdown",
                input="no",
                agents=(_agent(),),
                policy=AssignThenCompletePolicy(),
            )
        )
