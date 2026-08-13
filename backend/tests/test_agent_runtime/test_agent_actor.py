from __future__ import annotations

import asyncio

import pytest

from agent_runtime import AgentConfig, AgentMemory, AgentReport, AgentState, AgentWill
from agent_runtime.core.run_types import (
    AgentExecutionRequest,
    AgentExecutionResult,
    ContextSnapshot,
)
from agent_runtime.runtime.agent_actor import AgentActor
from agent_runtime.runtime.cancellation import CancellationScope, RunLease


pytestmark = [pytest.mark.unit, pytest.mark.agents]


def _agent(agent_id: str = "worker") -> AgentConfig:
    return AgentConfig(id=agent_id, name="Worker", system_prompt="Work")


def _request(agent_id: str = "worker", run_id: str = "run-1") -> AgentExecutionRequest:
    return AgentExecutionRequest(
        run_id=run_id,
        context_scope_id="scope-1",
        agent=_agent(agent_id),
        task="do work",
        input="do work",
        context=ContextSnapshot(scope_id="scope-1"),
        token_budget_remaining=100,
    )


class RecordingExecutor:
    def __init__(self) -> None:
        self.task_name = ""

    async def execute(self, request, *, emit, cancellation, lease):
        self.task_name = asyncio.current_task().get_name()
        await emit("model.output", {"content": "done"}, "model", None, None, None)
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=AgentReport(
                agent_id=request.agent.id,
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
            ),
            output="done",
            memory=AgentMemory(agent_id=request.agent.id, summary="done"),
        )


async def test_agent_actor_executes_mailbox_assignment_in_owned_task():
    emitted = []

    async def emit(*args):
        emitted.append(args)

    executor = RecordingExecutor()
    actor = AgentActor(
        run_id="run-1",
        agent_config=_agent(),
        executor=executor,
        emit=emit,
        cancellation=CancellationScope(),
        lease=RunLease("run-1"),
    )
    actor.start()

    result = await actor.assign(_request())
    await actor.stop()

    assert result.output == "done"
    assert executor.task_name == "runtime-actor:run-1:worker"
    assert emitted[0][0] == "model.output"


async def test_agent_actor_rejects_cross_run_or_cross_agent_assignment():
    async def emit(*_args):
        return None

    actor = AgentActor(
        run_id="run-1",
        agent_config=_agent(),
        executor=RecordingExecutor(),
        emit=emit,
        cancellation=CancellationScope(),
        lease=RunLease("run-1"),
    )
    actor.start()

    with pytest.raises(ValueError, match="does not match"):
        await actor.assign(_request(run_id="run-2"))
    with pytest.raises(ValueError, match="does not match"):
        await actor.assign(_request(agent_id="other"))

    await actor.stop()


class BlockingExecutor:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.stopped = asyncio.Event()

    async def execute(self, request, *, emit, cancellation, lease):
        self.started.set()
        try:
            await asyncio.Future()
        finally:
            self.stopped.set()


async def test_agent_actor_force_stop_cancels_inflight_assignment():
    async def emit(*_args):
        return None

    executor = BlockingExecutor()
    actor = AgentActor(
        run_id="run-1",
        agent_config=_agent(),
        executor=executor,
        emit=emit,
        cancellation=CancellationScope(),
        lease=RunLease("run-1"),
    )
    actor.start()
    assignment = asyncio.create_task(actor.assign(_request()))
    await executor.started.wait()

    await actor.stop(force=True)

    assert executor.stopped.is_set()
    assert assignment.cancelled() or isinstance(
        (await asyncio.gather(assignment, return_exceptions=True))[0], asyncio.CancelledError
    )
