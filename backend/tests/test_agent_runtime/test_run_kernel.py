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
from agent_runtime.runtime.run_store import InMemoryRunStore


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


async def _collect_events(handle):
    return [event async for event in handle.events()]


class CollectingSink:
    def __init__(self) -> None:
        self.events = []

    async def emit(self, event):
        self.events.append(event)


async def test_run_completes_once_and_commits_structured_context():
    context_store = InMemoryContextStore()
    run_store = InMemoryRunStore()
    engine = RuntimeEngine(
        agent_executor=SuccessfulExecutor(),
        context_store=context_store,
        run_store=run_store,
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
    run_store = InMemoryRunStore()
    sink = CollectingSink()
    engine = RuntimeEngine(agent_executor=executor, run_store=run_store, event_sink=sink)
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
    run_store = InMemoryRunStore()
    engine = RuntimeEngine(agent_executor=SuccessfulExecutor(), run_store=run_store)
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
    )
    with pytest.raises(ValueError):
        RuntimeLimits(max_decisions=0)
