"""Deterministic harness regressions through the shared Kernel and AgentLoop."""

import asyncio

from agent_contracts.harness import ExecutionLimits
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine, RuntimeLimits
from agent_runtime.core.types import ToolResult
from agent_runtime.runtime.run_watchdog import RunWatchdog
from agent_subsystems.execution.activity import ProtocolFrameGuard
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from model_provider import StreamChunk


class ScriptedModel:
    def __init__(self, rounds=0, *, delay=0, text="Finished.", finish="stop"):
        self.rounds, self.delay, self.text, self.finish = rounds, delay, text, finish
        self.calls = 0
        self.closed = 0

    async def chat_stream(self, **kwargs):
        self.calls += 1
        try:
            await asyncio.sleep(self.delay)
            if self.calls <= self.rounds:
                yield StreamChunk(tool_call={"index": 0, "id": f"c{self.calls}",
                    "type": "function", "function": {"name": "test.read", "arguments": "{}"}})
            else:
                for part in self.text:
                    yield StreamChunk(content=part)
            yield StreamChunk(finish_reason=self.finish,
                              usage={"prompt_tokens": 10, "completion_tokens": 2})
        finally:
            self.closed += 1


class Tool:
    def __init__(self):
        self.calls = 0

    async def list_tools(self):
        return [{"type": "function", "function": {"name": "test.read"}}]

    async def execute(self, call):
        self.calls += 1
        return ToolResult(call_id=call.call_id, success=True, result={"value": self.calls})


async def run_model(model, *, execution_limits=None, runtime_limits=None):
    tool = Tool()
    engine = RuntimeEngine(agent_executor=AgentLoopExecutor(
        model_provider=model, tool_executor=tool, execution_limits=execution_limits),
        limits=runtime_limits)
    handle = await engine.start(RunRequest("scope", "Inspect the project",
        (AgentConfig("local", "Local", "Be accurate"),), SingleAgentPolicy()))
    result = await asyncio.wait_for(handle.result(), 5)
    events = [event async for event in handle.events()]
    await engine.shutdown()
    return result, events, tool


async def test_twenty_five_tool_rounds_complete_and_usage_is_counted_once():
    model = ScriptedModel(25)
    result, events, tool = await run_model(model)
    assert result.state.value == "completed"
    assert model.calls == 26 and tool.calls == 25
    assert result.usage.total_tokens == 26 * 12
    assert result.counters == {"model_requests": 26, "tool_rounds": 25, "tool_calls": 25}
    assert len([e for e in events if e.type == "system.run_completed"]) == 1


async def test_explicit_cap_has_no_extra_summary_request():
    model = ScriptedModel(25)
    result, _, tool = await run_model(model, execution_limits=ExecutionLimits(max_model_turns=3))
    assert result.reason_code == "model_turn_budget_exhausted"
    assert result.state.value == "failed"
    assert model.calls == tool.calls == 3
    assert result.usage.total_tokens == 36


async def test_model_activity_outlives_idle_timeout_but_request_timeout_is_real():
    result, _, _ = await run_model(ScriptedModel(3, delay=0.03),
        runtime_limits=RuntimeLimits(idle_time_seconds=0.015),
        execution_limits=ExecutionLimits(request_timeout_seconds=0.2))
    assert result.state.value == "completed"
    model = ScriptedModel(delay=1)
    result, _, _ = await run_model(model,
        execution_limits=ExecutionLimits(request_timeout_seconds=0.03))
    assert result.reason_code == "model_timeout"
    assert model.closed == 1


async def test_wall_deadline_cannot_be_extended_by_active_phases():
    result, _, _ = await run_model(ScriptedModel(100, delay=0.02),
        runtime_limits=RuntimeLimits(wall_time_seconds=0.08, idle_time_seconds=0.01))
    assert result.state.value == "failed"
    assert result.reason_code == "wall_time_exceeded"


async def test_protocol_frames_are_neither_executed_nor_streamed():
    result, events, tool = await run_model(ScriptedModel(text=
        '<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="test.read">'))
    assert result.reason_code == "provider_protocol_error"
    assert tool.calls == 0
    assert not any("DSML" in str(e.payload) for e in events if e.type == "agent.token")


def test_protocol_examples_and_partial_prefixes_are_not_rejected():
    text = 'Example:\n```xml\n<｜｜DSML｜｜tool_calls>\n```\nEnd.'
    guard = ProtocolFrameGuard()
    output = "".join(guard.push(c) for c in text) + guard.push("", final=True)
    assert not guard.invalid and output == text


def test_watchdog_active_phase_does_not_mask_another_expired_phase(monkeypatch):
    now = [0.0]
    monkeypatch.setattr("agent_runtime.runtime.run_watchdog.time.monotonic", lambda: now[0])
    dog = RunWatchdog(RuntimeLimits(wall_time_seconds=20, idle_time_seconds=1,
                                    cancellation_grace_seconds=0.1), None)
    dog.phase_started("a", "model", 5)
    dog.phase_started("b", "tool", 10)
    now[0] = 2
    assert dog.reason() is None
    now[0] = 6
    assert dog.reason() == "model_timeout"


async def test_output_length_stop_is_distinct_from_run_token_budget():
    result, _, _ = await run_model(ScriptedModel(finish="length"))
    assert result.reason_code == "output_token_limit_exceeded"
    assert result.usage.total_tokens == 12
