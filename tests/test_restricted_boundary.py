"""Failure and protocol boundaries without native privileges or live models."""

import asyncio
import json
import struct
from types import SimpleNamespace

import pytest

from agent_adapters.local.worker_protocol import ProtocolError, decode, encode, response_result
from agent_contracts.execution import ToolSpec
from agent_contracts.harness import ExecutionStopped
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_runtime.core.types import ToolCall
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.tools.invoker import AuthorizedToolInvoker
from agent_subsystems.tools.registry import ToolRegistry
from test_harness_execution import ScriptedModel, Tool


@pytest.mark.parametrize("data", [b"", b"abc", struct.pack("!I", 100) + b"{}",
    struct.pack("!I", 2) + b"{}extra", encode({"version": 2}, 100),
    struct.pack("!I", 1) + b"\xff", struct.pack("!I", 21) + b'{"version":1,"x":NaN}'])
def test_corrupt_worker_frames_fail(data):
    with pytest.raises(ProtocolError):
        decode(data, 100)


def test_worker_utf8_boundaries_are_byte_limited():
    value = {"version": 1, "text": "中文\n"}
    data = encode(value, 100)
    assert decode(data, len(data) - 4) == value
    with pytest.raises(ProtocolError):
        encode(value, len(data) - 5)


@pytest.mark.parametrize("body", [b'{"version":true}', b'{"version":1,"version":1}',
                                 b'{"version":1,"x":NaN}'])
def test_worker_rejects_ambiguous_json(body):
    with pytest.raises(ProtocolError):
        decode(struct.pack("!I", len(body)) + body, 1000)


@pytest.mark.parametrize("extra", [{"ok": True}, {"ok": True, "result": []},
    {"ok": False, "error_code": "failed"}, {"ok": False, "error_code": 7, "error": "bad"}])
def test_malformed_response_is_protocol_failure(extra):
    with pytest.raises(ProtocolError):
        response_result(encode({"version": 1, "request_id": "call", **extra}, 1000), "call")


async def test_invalid_arguments_are_rejected_before_authorization():
    calls = []
    schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}
    authorization = SimpleNamespace(authorize=lambda request: calls.append(request) or "allow")
    invoker = AuthorizedToolInvoker(ToolRegistry(), {"file.read": ToolSpec("file.read", "", schema, "files")},
        authorization, SimpleNamespace(check=lambda: None), Redactor())
    result = await invoker.execute(ToolCall("file.read", {"path": 3}, "call"))
    assert not result.success and result.result["error_code"] == "invalid_arguments"
    assert not calls


async def test_isolation_stop_is_not_recoverable_tool_error():
    registry = ToolRegistry()

    async def broken():
        raise ExecutionStopped("worker_protocol_error")

    schema = {"type": "object"}
    registry.register("broken", "", schema, broken)
    invoker = AuthorizedToolInvoker(registry, {"broken": ToolSpec("broken", "", schema, "files")},
        SimpleNamespace(authorize=lambda request: "allow"), SimpleNamespace(check=lambda: None), Redactor())
    with pytest.raises(ExecutionStopped, match="worker_protocol_error"):
        await invoker.execute(ToolCall("broken", {}, "call"))


async def test_unconfirmed_drain_cannot_commit_success():
    calls = []

    class Guard:
        async def prepare(self, context):
            calls.append("prepare")

        async def drain(self):
            calls.append("drain")
            raise ExecutionStopped("isolation_cleanup_unconfirmed")

    tool = Tool()
    tool.context = object()
    engine = RuntimeEngine(agent_executor=AgentLoopExecutor(model_provider=ScriptedModel(),
        tool_executor=tool, execution_isolation=Guard()))
    try:
        handle = await engine.start(RunRequest("scope", "task", (AgentConfig("local", "Local", ""),),
                                                SingleAgentPolicy()))
        result = await handle.result()
        events = [event async for event in handle.events()]
        assert calls == ["prepare", "drain"]
        assert result.state.value == "failed" and result.reason_code == "isolation_cleanup_unconfirmed"
        assert result.context_version == 0
        assert sum(event.type == "system.run_failed" for event in events) == 1
        assert not any(event.type == "system.run_completed" for event in events)
    finally:
        await engine.shutdown()


async def test_handle_fail_preserves_reason_and_single_terminal():
    model = ScriptedModel(delay=30)
    engine = RuntimeEngine(agent_executor=AgentLoopExecutor(model_provider=model, tool_executor=Tool()))
    try:
        handle = await engine.start(RunRequest("scope", "task", (AgentConfig("local", "Local", ""),),
                                                SingleAgentPolicy()))
        while not model.calls:
            await asyncio.sleep(0)
        result = await asyncio.wait_for(handle.fail("permission_lease_invalid"), 5)
        assert result.state.value == "failed" and result.reason_code == "permission_lease_invalid"
        assert await handle.fail("late_reason") == result
        events = [event async for event in handle.events()]
        assert sum(event.type == "system.run_failed" for event in events) == 1
        assert "permission_lease_invalid" in json.dumps(result.reason_code)
    finally:
        await engine.shutdown()


async def test_cleanup_failure_after_cancel_does_not_leave_actor_waiting():
    model = ScriptedModel(delay=30)
    tool = Tool()
    tool.context = object()

    class RevokedGuard:
        async def prepare(self, context):
            pass

        async def drain(self):
            raise ExecutionStopped("permission_lease_invalid")

    engine = RuntimeEngine(agent_executor=AgentLoopExecutor(model_provider=model,
        tool_executor=tool, execution_isolation=RevokedGuard()))
    try:
        handle = await engine.start(RunRequest("scope", "task", (AgentConfig("local", "Local", ""),),
                                                SingleAgentPolicy()))
        while not model.calls:
            await asyncio.sleep(0)
        result = await asyncio.wait_for(handle.fail("permission_lease_invalid"), 3)
        assert result.state.value == "failed" and result.reason_code == "permission_lease_invalid"
    finally:
        await engine.shutdown()
