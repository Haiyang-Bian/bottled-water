from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from agent_runtime import (
    AgentConfig,
    AgentReport,
    AgentState,
    AgentWill,
    RuntimeEngine,
    SingleAgentPolicy,
)
from agent_runtime.core.run_types import AgentExecutionResult
from app.services.conversation_session_manager import ConversationRunManager
from app.services.runtime.event_projection import project_runtime_event
from app.services.runtime_service import OrchestratorService, RuntimeBinding
from db.base import Base
from db.models import Conversation, Message, User


pytestmark = [pytest.mark.integration, pytest.mark.runtime]


class StreamingExecutor:
    def __init__(self, *, block: bool = False) -> None:
        self.block = block
        self.started = asyncio.Event()

    async def execute(self, request, *, emit, cancellation, lease):
        self.started.set()
        await emit(
            "message_start",
            {"agent_id": request.agent.id, "agent_name": request.agent.name},
            f"agent:{request.agent.id}",
            None,
            None,
            None,
        )
        await emit(
            "agent.thinking",
            {"agent_id": request.agent.id, "thinking": "checking"},
            f"agent:{request.agent.id}",
            None,
            None,
            None,
        )
        await emit(
            "agent.token",
            {"agent_id": request.agent.id, "token": "done"},
            f"agent:{request.agent.id}",
            None,
            None,
            None,
        )
        if self.block:
            await asyncio.Future()
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=AgentReport(
                agent_id=request.agent.id,
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
            ),
            output="done",
        )


async def _database(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'manager.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        user = User(
            id="user",
            email="manager@example.com",
            username="manager",
            password_hash="x",
        )
        conversation = Conversation(
            id="conversation",
            creator_id=user.id,
            chat_type="single",
            title="Runtime",
            extra={"scheduling_strategy": "single_agent"},
        )
        db.add_all([user, conversation])
        await db.commit()
    return engine, factory


def _binding(executor) -> RuntimeBinding:
    return RuntimeBinding(
        engine=RuntimeEngine(agent_executor=executor),
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy_factory=SingleAgentPolicy,
        scheduling_strategy="single_agent",
    )


async def _prepare_manager(tmp_path, executor):
    engine, factory = await _database(tmp_path)
    manager = ConversationRunManager(session_factory=factory)
    binding = _binding(executor)
    agent = SimpleNamespace(id="agent", name="Agent", type="worker")
    async with factory() as db:
        conversation = await db.get(Conversation, "conversation")
        with patch.object(
            manager,
            "_recover_abandoned_generation_if_needed",
            new=AsyncMock(return_value=False),
        ), patch.object(
            OrchestratorService,
            "_get_conversation_agents",
            new=AsyncMock(return_value=[agent]),
        ), patch.object(
            OrchestratorService,
            "create_engine",
            new=AsyncMock(return_value=binding),
        ):
            assert await manager.get_or_create_engine(db, conversation) is binding
    return engine, factory, manager


async def test_run_manager_projects_streams_and_clears_active_run(tmp_path):
    executor = StreamingExecutor()
    engine, factory, manager = await _prepare_manager(tmp_path, executor)
    emitted = []

    async def collect(_sink, event):
        emitted.append(event)

    try:
        with patch("app.events.WebSocketSink.emit", new=collect):
            await manager.start_generation(
                "conversation",
                "work",
                user_message_id="message-1",
                thinking_enabled=True,
            )
            task = manager._running_tasks["conversation"]
            await task
            await asyncio.sleep(0.05)

        assert manager.is_generation_running("conversation") is False
        assert manager.get_run_status("conversation") is None
        assert {event.type for event in emitted} >= {
            "message_start",
            "agent.token",
            "agent.thinking",
        }
        async with factory() as db:
            conversation = await db.get(Conversation, "conversation")
            assert conversation.active_run_id is None
            assert conversation.generation_status == "idle"
            messages = list(
                (
                    await db.scalars(
                        select(Message).where(Message.conversation_id == "conversation")
                    )
                ).all()
            )
            assert any(message.content.get("text") == "done" for message in messages)
    finally:
        await engine.dispose()


async def test_run_manager_cancellation_converges(tmp_path):
    executor = StreamingExecutor(block=True)
    engine, factory, manager = await _prepare_manager(tmp_path, executor)
    try:
        with patch("app.events.WebSocketSink.emit", new=AsyncMock()):
            await manager.start_generation("conversation", "wait")
            await executor.started.wait()
            assert await manager.cancel_generation("conversation") is True
            await asyncio.sleep(0.05)

        async with factory() as db:
            conversation = await db.get(Conversation, "conversation")
            assert conversation.active_run_id is None
            assert conversation.generation_status == "cancelled"
    finally:
        await engine.dispose()


def test_event_projection_preserves_frontend_protocol_names():
    from agent_runtime import EventEnvelope

    started = project_runtime_event(
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=1,
            type="system.run_started",
            payload={},
        )
    )
    proposal = project_runtime_event(
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=2,
            type="scheduler.proposal",
            payload={"action": "assign", "target_agent_ids": ["agent"], "task": "work"},
        )
    )

    assert started.type == "system.session_started"
    assert proposal.type == "scheduler.decision"
    assert proposal.payload["decision"]["target_agent_id"] == "agent"
