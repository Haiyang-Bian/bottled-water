from __future__ import annotations

import asyncio

import pytest

from agent_runtime import (
    AgentConfig,
    AgentReport,
    AgentState,
    AgentWill,
    RunRequest,
    RunState,
    RuntimeEngine,
    RuntimeLimits,
    SchedulingProposal,
    Usage,
    AgentLoopExecutor,
)
from agent_runtime.core.run_types import AgentExecutionResult
from agent_runtime.runtime.adapter_isolation import (
    AdapterNotCancellableError,
    run_cancellable_adapter,
)
from agent_runtime.runtime.cancellation import CancellationScope, RunLease
from model_provider.core.interfaces import StreamChunk


pytestmark = [pytest.mark.unit, pytest.mark.runtime]


def _request(policy, *, run_id: str) -> RunRequest:
    return RunRequest(
        run_id=run_id,
        context_scope_id="scope",
        input="work",
        agents=(AgentConfig(id="worker", name="Worker", system_prompt="work"),),
        policy=policy,
    )


class BlockingPolicy:
    async def propose(self, snapshot, trigger):
        await asyncio.Future()


class WaitPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="wait")


class TokenPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="wait", usage=Usage(completion_tokens=10))


class AssignForeverPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="assign", target_agent_ids=("worker",), task="work")


class NoopExecutor:
    async def execute(self, request, *, emit, cancellation, lease):
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=AgentReport(
                agent_id=request.agent.id,
                state=AgentState.WAITING,
                will=AgentWill.WAIT,
            ),
            progress=False,
        )


class StreamingWithoutProgressExecutor:
    async def execute(self, request, *, emit, cancellation, lease):
        while True:
            await emit("model.token", {"content": "x"}, "model", None, None, None)
            await asyncio.sleep(0.002)


async def _result_for(policy, executor, limits, run_id):
    engine = RuntimeEngine(agent_executor=executor, limits=limits)
    handle = await engine.start(_request(policy, run_id=run_id))
    return await asyncio.wait_for(handle.result(), timeout=1)


@pytest.mark.parametrize(
    ("policy", "executor", "limits", "reason"),
    [
        (
            BlockingPolicy(),
            NoopExecutor(),
            RuntimeLimits(wall_time_seconds=0.02, idle_time_seconds=1),
            "wall_time_exceeded",
        ),
        (
            AssignForeverPolicy(),
            StreamingWithoutProgressExecutor(),
            RuntimeLimits(wall_time_seconds=1, idle_time_seconds=0.02),
            "idle_timeout",
        ),
        (
            WaitPolicy(),
            NoopExecutor(),
            RuntimeLimits(max_decisions=2, max_no_progress=10),
            "decision_budget_exhausted",
        ),
        (
            TokenPolicy(),
            NoopExecutor(),
            RuntimeLimits(max_total_tokens=10),
            "token_budget_exhausted",
        ),
        (
            AssignForeverPolicy(),
            NoopExecutor(),
            RuntimeLimits(max_no_progress=2),
            "no_progress",
        ),
    ],
)
async def test_each_budget_has_a_stable_failure_reason(policy, executor, limits, reason):
    result = await _result_for(policy, executor, limits, f"run-{reason}")

    assert result.state is RunState.FAILED
    assert result.reason_code == reason


class ProgressThenCompletePolicy:
    async def propose(self, snapshot, trigger):
        if len(snapshot.reports) >= 2:
            return SchedulingProposal(action="complete")
        return SchedulingProposal(action="assign", target_agent_ids=("worker",), task="step")


class SlowProgressExecutor(NoopExecutor):
    async def execute(self, request, *, emit, cancellation, lease):
        await asyncio.sleep(0.012)
        result = await super().execute(
            request, emit=emit, cancellation=cancellation, lease=lease
        )
        return AgentExecutionResult(
            agent_id=result.agent_id,
            report=result.report,
            output="progress",
            progress=True,
        )


async def test_real_progress_resets_idle_deadline():
    result = await _result_for(
        ProgressThenCompletePolicy(),
        SlowProgressExecutor(),
        RuntimeLimits(wall_time_seconds=1, idle_time_seconds=0.02),
        "run-progress",
    )

    assert result.state is RunState.COMPLETED


async def test_side_effecting_adapter_must_expose_termination():
    async def operation():
        return "unsafe"

    with pytest.raises(AdapterNotCancellableError, match="adapter_not_cancellable"):
        await run_cancellable_adapter(
            operation(),
            cancellation=CancellationScope(),
            lease=RunLease("run"),
            has_external_side_effects=True,
        )


async def test_cancellable_adapter_invokes_termination_hook():
    cancellation = CancellationScope()
    terminated = asyncio.Event()

    async def operation():
        await asyncio.Future()

    async def terminate():
        terminated.set()

    task = asyncio.create_task(
        run_cancellable_adapter(
            operation(),
            cancellation=cancellation,
            lease=RunLease("run"),
            terminate=terminate,
            has_external_side_effects=True,
        )
    )
    await asyncio.sleep(0)
    cancellation.cancel("user_cancelled")

    with pytest.raises(asyncio.CancelledError):
        await task
    assert terminated.is_set()


class EndlessStreamingProvider:
    def __init__(self) -> None:
        self.closed = asyncio.Event()

    def count_tokens(self, text: str) -> int:
        return len(text)

    async def chat_stream(self, **_kwargs):
        try:
            while True:
                yield StreamChunk(content="xxxx")
                await asyncio.sleep(0)
        finally:
            self.closed.set()


async def test_stream_is_closed_when_remaining_token_budget_is_exhausted():
    provider = EndlessStreamingProvider()
    engine = RuntimeEngine(
        agent_executor=AgentLoopExecutor(model_provider=provider),
        limits=RuntimeLimits(max_total_tokens=20),
    )
    handle = await engine.start(_request(AssignForeverPolicy(), run_id="run-stream-budget"))

    result = await asyncio.wait_for(handle.result(), timeout=1)

    assert result.state is RunState.FAILED
    assert result.reason_code == "token_budget_exhausted"
    assert provider.closed.is_set()
