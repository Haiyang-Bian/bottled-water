import asyncio

import pytest
from model_provider import ChatResponse

from agent_runtime.context.blackboard import BlackboardManager
from agent_runtime.core.interfaces import AgentContextBuildResult
from agent_runtime.core.protocol import AGENT_REPORT, CONTROL_ASSIGN, CONTROL_COMPLETE
from agent_runtime.core.types import AgentConfig, AgentState, Event
from agent_runtime.runtime.agent_actor import AgentActor
from agent_runtime.runtime.event_dispatcher import EventDispatcher


@pytest.mark.asyncio
async def test_agent_actor_runs_assignment_and_publishes_report(mock_provider, mock_tool_executor):
    mock_provider.responses = [
        ChatResponse(
            content='done\n```status_report\n{"state": "completed", "will": "complete", "confidence": 0.9}\n```'
        )
    ]
    bus = EventDispatcher()
    blackboard = BlackboardManager(event_bus=bus)
    events: list[Event] = []
    bus.subscribe("*", lambda event: _append(events, event), target="*")
    actor = AgentActor(
        session_id="sess_actor",
        agent_config=AgentConfig(id="coder", name="Coder", system_prompt="You code."),
        model_provider=mock_provider,
        event_bus=bus,
        tool_executor=mock_tool_executor,
        blackboard_mgr=blackboard,
        use_streaming=False,
    )
    actor.start()

    await bus.publish(
        Event(
            type=CONTROL_ASSIGN,
            payload={
                "task": "write a helper",
                "task_input": {
                    "user_request": "build feature",
                    "assigned_task": "write a helper",
                    "upstream_outputs": {"planner": {"output": "plan done"}},
                },
            },
            source="scheduler",
            target="coder",
        )
    )
    report_event = await _wait_for(events, AGENT_REPORT)
    await actor.stop()

    assert report_event.payload["agent_id"] == "coder"
    assert report_event.payload["report"]["state"] == AgentState.COMPLETED.value
    assert report_event.payload["input"]["user_request"] == "build feature"
    assert report_event.payload["output"]["work_product"]
    assert report_event.payload["tool_events"] == []
    stored = await blackboard.get("sess_actor")
    assert stored is not None
    assert any(item.get("type") == "agent_work" for item in stored["raw_history"])
    history = next(item for item in stored["raw_history"] if item.get("type") == "agent_work")
    assert history["input"]["assigned_task"] == "write a helper"
    assert history["output"]["work_product"]


@pytest.mark.asyncio
async def test_agent_actor_report_carries_stream_message_id(mock_provider, mock_tool_executor):
    mock_provider.responses = [
        ChatResponse(
            content='done\n```status_report\n{"state": "completed", "will": "complete"}\n```'
        )
    ]
    bus = EventDispatcher()
    events: list[Event] = []
    bus.subscribe("*", lambda event: _append(events, event), target="*")
    actor = AgentActor(
        session_id="sess_actor_stream",
        agent_config=AgentConfig(id="coder", name="Coder", system_prompt="You code."),
        model_provider=mock_provider,
        event_bus=bus,
        tool_executor=mock_tool_executor,
        use_streaming=True,
    )
    actor.start()

    await bus.publish(
        Event(
            type=CONTROL_ASSIGN,
            payload={"task": "write a helper"},
            source="scheduler",
            target="coder",
        )
    )
    report_event = await _wait_for(events, AGENT_REPORT)
    await actor.stop()

    stream_id = str(report_event.payload.get("stream_message_id") or "")
    start_event = next(event for event in events if event.type == "message_start")
    assert stream_id.startswith("stream-coder-")
    assert report_event.payload["agent_message_id"] == stream_id
    assert start_event.payload["agent_message_id"] == stream_id


@pytest.mark.asyncio
async def test_agent_actor_passes_assignment_context_metadata(mock_provider, mock_tool_executor):
    captured: dict = {}

    class Provider:
        async def build_agent_context(self, request):
            captured["request"] = request
            return AgentContextBuildResult(
                messages=[
                    {"role": "system", "content": "system with context"},
                    {"role": "user", "content": "user with memory"},
                ]
            )

    mock_provider.responses = [
        ChatResponse(
            content='done\n```status_report\n{"state": "completed", "will": "complete"}\n```'
        )
    ]
    bus = EventDispatcher()
    events: list[Event] = []
    bus.subscribe("*", lambda event: _append(events, event), target="*")
    actor = AgentActor(
        session_id="sess_actor_context",
        agent_config=AgentConfig(id="coder", name="Coder", system_prompt="You code."),
        model_provider=mock_provider,
        event_bus=bus,
        tool_executor=mock_tool_executor,
        use_streaming=False,
        context_provider=Provider(),
    )
    actor.start()

    await bus.publish(
        Event(
            type=CONTROL_ASSIGN,
            payload={
                "task": "write a helper",
                "task_input": {
                    "user_request": "build feature",
                    "assigned_task": "write a helper",
                    "upstream_outputs": {"planner": {"output": "plan done"}},
                },
                "context_metadata": {
                    "conversation_id": "conv-1",
                    "user_message_id": "msg-1",
                    "client_message_id": "client-1",
                    "visible_content": "visible text",
                },
            },
            source="scheduler",
            target="coder",
        )
    )
    await _wait_for(events, AGENT_REPORT)
    await actor.stop()

    assert captured["request"].metadata["user_message_id"] == "msg-1"
    assert captured["request"].metadata["client_message_id"] == "client-1"
    assert captured["request"].metadata["visible_content"] == "visible text"
    assert captured["request"].metadata["task_input"]["upstream_outputs"]["planner"]["output"] == "plan done"
    thinking_event = next(event for event in events if event.type == "agent.thinking")
    assert thinking_event.payload["user_message_id"] == "msg-1"
    assert thinking_event.payload["client_message_id"] == "client-1"


@pytest.mark.asyncio
async def test_agent_actor_exits_on_control_complete(mock_provider, mock_tool_executor):
    bus = EventDispatcher()
    events: list[Event] = []
    bus.subscribe("*", lambda event: _append(events, event), target="*")
    actor = AgentActor(
        session_id="sess_complete",
        agent_config=AgentConfig(id="reviewer", name="Reviewer", system_prompt="You review."),
        model_provider=mock_provider,
        event_bus=bus,
        tool_executor=mock_tool_executor,
        use_streaming=False,
    )
    task = actor.start()

    await bus.publish(
        Event(
            type=CONTROL_COMPLETE,
            payload={"reason": "workflow_done"},
            source="scheduler",
            target="reviewer",
        )
    )
    report_event = await _wait_for(events, AGENT_REPORT)
    await asyncio.wait_for(task, timeout=1)
    actor.mailbox.close()

    assert report_event.payload["agent_id"] == "reviewer"
    assert report_event.payload["report"]["state"] == AgentState.COMPLETED.value


async def _append(target: list[Event], event: Event) -> None:
    target.append(event)


async def _wait_for(events: list[Event], event_type: str) -> Event:
    for _ in range(50):
        for event in events:
            if event.type == event_type:
                return event
        await asyncio.sleep(0.02)
    raise AssertionError(f"event {event_type} was not published")
