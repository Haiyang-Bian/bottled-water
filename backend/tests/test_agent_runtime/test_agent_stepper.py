import asyncio
from types import SimpleNamespace

import pytest

from agent_runtime.core.protocol import CONTROL_CANCEL, CONTROL_PAUSE, CONTROL_RESUME
from agent_runtime.core.types import AgentConfig, AgentReport, AgentState, AgentWill, Event
from agent_subsystems.execution.agent_loop import AgentLoop
from agent_runtime.runtime.agent_stepper import AgentStepper
from agent_runtime.runtime.mailbox import Mailbox
from model_provider.core.interfaces import ChatResponse, StreamChunk


class FakeLoop:
    def __init__(self) -> None:
        self.agent = SimpleNamespace(id="worker")
        self.completed = False

    async def run(self, task, blackboard_view, *, emit_event=None, **kwargs):
        if emit_event:
            await emit_event(Event(type="agent.token", payload={"token": "first"}))
            await emit_event(Event(type="agent.token", payload={"token": "second"}))
        self.completed = True
        return {
            "work_product": f"done {task}",
            "status_report": AgentReport(
                agent_id="worker",
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
            ),
        }


@pytest.mark.asyncio
async def test_agent_stepper_cancels_between_loop_events():
    mailbox = Mailbox("worker")
    loop = FakeLoop()
    stepper = AgentStepper(loop, mailbox)
    emitted: list[Event] = []

    async def emit_event(event: Event) -> None:
        emitted.append(event)
        if len(emitted) == 1:
            await mailbox.send(Event(type=CONTROL_CANCEL, payload={}, target="worker"))

    result = await stepper.run_assignment("write code", {}, emit_event=emit_event)

    assert result.interrupted is True
    assert result.report.state == AgentState.FAILED
    assert loop.completed is False
    assert [event.payload["token"] for event in emitted] == ["first"]


@pytest.mark.asyncio
async def test_agent_stepper_waits_while_paused_before_assignment():
    mailbox = Mailbox("worker")
    loop = FakeLoop()
    stepper = AgentStepper(loop, mailbox)

    await mailbox.send(Event(type=CONTROL_PAUSE, payload={}, target="worker"))
    task = asyncio.create_task(stepper.run_assignment("write code", {}))
    await asyncio.sleep(0.05)

    assert task.done() is False
    assert stepper.state == AgentState.PAUSED

    await mailbox.send(Event(type=CONTROL_RESUME, payload={}, target="worker"))
    result = await asyncio.wait_for(task, timeout=1)

    assert result.interrupted is False
    assert result.report.state == AgentState.COMPLETED
    assert loop.completed is True


@pytest.mark.asyncio
async def test_agent_stepper_cancels_after_model_before_tool_execution():
    mailbox = Mailbox("worker")
    model = CancelAfterModelProvider(mailbox)
    executor = FakeToolExecutor()
    agent_loop = AgentLoop(
        AgentConfig(
            id="worker",
            name="Worker",
            system_prompt="Use tools when needed.",
            tools=["sandbox.run"],
        ),
        model,
        use_streaming=False,
    )
    stepper = AgentStepper(agent_loop, mailbox)

    result = await stepper.run_assignment(
        "run code",
        {},
        tool_executor=executor,
    )

    assert result.interrupted is True
    assert result.report.state == AgentState.FAILED
    assert executor.calls == []


class CancelAfterModelProvider:
    def __init__(self, mailbox: Mailbox) -> None:
        self.mailbox = mailbox

    async def chat(self, **_kwargs):
        await self.mailbox.send(Event(type=CONTROL_CANCEL, payload={}, target="worker"))
        return ChatResponse(
            content="",
            tool_calls=[
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "sandbox.run", "arguments": "{}"},
                }
            ],
        )

    async def chat_stream(self, **kwargs):
        response = await self.chat(**kwargs)
        for tool_call in response.tool_calls or []:
            yield StreamChunk(tool_call=tool_call)
        yield StreamChunk(content=response.content or "", finish_reason="stop")


class FakeToolExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def list_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "sandbox.run",
                    "description": "Run code",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

    async def execute(self, tool_call):
        self.calls.append(tool_call)
        return {"status": "succeeded"}
