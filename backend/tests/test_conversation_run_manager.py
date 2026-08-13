from __future__ import annotations

import asyncio
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete, select
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
from agent_runtime.core.types import Event as RuntimeEvent
from app.services.conversation_run_manager import (
    ConversationRunManager,
    RuntimeProjectionGapError,
)
from app.persistence.runtime_journal import SQLRunJournal
from app.services.runtime.event_projection import project_runtime_event
from app.services.runtime_service import OrchestratorService, RuntimeBinding
from db.base import Base
from db.models import Conversation, Message, RuntimeEventConsumer, User


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


def _binding(executor, journal) -> RuntimeBinding:
    return RuntimeBinding(
        engine=RuntimeEngine(agent_executor=executor, run_journal=journal),
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy_factory=SingleAgentPolicy,
        scheduling_strategy="single_agent",
    )


async def test_runtime_provider_preserves_deepseek_model_options():
    provider = SimpleNamespace(
        provider_type="deepseek",
        base_url="https://api.deepseek.com",
        api_key_ref="stored-key",
        config={},
        status="active",
        name="DeepSeek",
    )
    config = SimpleNamespace(
        id="deepseek-config",
        provider=provider,
        model_id="deepseek-v4-pro",
        temperature_default=0.4,
        max_output_tokens=4096,
        config={"thinking_enabled": True, "reasoning_effort": "max"},
    )
    db = SimpleNamespace(scalar=AsyncMock(return_value=config))

    with patch(
        "app.services.model_config_resolver.resolve_api_key",
        new=AsyncMock(return_value="stored-key"),
    ), patch(
        "app.services.runtime_service.create_provider",
        side_effect=lambda value: value,
    ):
        runtime_config = await OrchestratorService.create_provider_from_config(
            db,
            "deepseek-config",
        )

    assert runtime_config.provider == "deepseek"
    assert runtime_config.extra == {
        "thinking_enabled": True,
        "reasoning_effort": "max",
    }


async def _prepare_manager(tmp_path, executor):
    engine, factory = await _database(tmp_path)
    manager = ConversationRunManager(session_factory=factory)
    binding = _binding(executor, SQLRunJournal(factory))
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
            "system.session_completed",
            "generation_finished",
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
            assert all("thinking" not in (message.content or {}) for message in messages)
            consumer = await db.get(
                RuntimeEventConsumer,
                {
                    "consumer_name": "agenthub_generation_projection",
                    "run_id": conversation.extra["runtime"]["generations"][-1]["id"],
                },
            )
            assert consumer is not None
            assert consumer.last_sequence > 0
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
    failed = project_runtime_event(
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=3,
            type="system.run_failed",
            payload={"reason_code": "event_store_error"},
        )
    )

    assert started.type == "system.session_started"
    assert started.payload["runtime_event_id"]
    assert started.payload["runtime_sequence"] == 1
    assert started.payload["runtime_replayed"] is False
    assert proposal.type == "scheduler.decision"
    assert proposal.payload["decision"]["target_agent_id"] == "agent"
    assert failed.type == "system.session_error"
    assert failed.payload["reason_code"] == "event_store_error"


async def test_generation_projection_skips_duplicates_and_rejects_sequence_gaps(tmp_path):
    executor = StreamingExecutor()
    engine, factory, manager = await _prepare_manager(tmp_path, executor)
    emitted = []

    async def collect(_sink, event):
        emitted.append(event)

    try:
        with patch("app.events.WebSocketSink.emit", new=collect):
            await manager.start_generation("conversation", "work")
            await manager._running_tasks["conversation"]
            await asyncio.sleep(0.05)

        durable = next(
            event
            for event in emitted
            if isinstance(event.payload, dict) and event.payload.get("runtime_sequence")
        )
        generation_id = str(durable.payload["runtime_run_id"])
        assert await manager._record_generation_event(
            "conversation", generation_id, durable
        ) is False

        async with factory() as db:
            consumer = await db.get(
                RuntimeEventConsumer,
                {
                    "consumer_name": "agenthub_generation_projection",
                    "run_id": generation_id,
                },
            )
            last_sequence = consumer.last_sequence

        gap = RuntimeEvent(
            type="agent.report",
            payload={
                "generation_id": generation_id,
                "runtime_run_id": generation_id,
                "runtime_event_id": "gap-event",
                "runtime_sequence": last_sequence + 2,
                "agent_id": "agent",
            },
        )
        with pytest.raises(RuntimeProjectionGapError):
            await manager._record_generation_event("conversation", generation_id, gap)
    finally:
        await engine.dispose()


async def test_generation_projection_rolls_back_read_model_and_cursor_together(tmp_path):
    executor = StreamingExecutor()
    engine, factory, manager = await _prepare_manager(tmp_path, executor)
    try:
        with patch("app.events.WebSocketSink.emit", new=AsyncMock()):
            await manager.start_generation("conversation", "work")
            await manager._running_tasks["conversation"]
            await asyncio.sleep(0.05)

        async with factory() as db:
            conversation = await db.get(Conversation, "conversation")
            generation = conversation.extra["runtime"]["generations"][-1]
            generation_id = str(generation["id"])
            before_counts = dict(generation.get("event_counts") or {})
            consumer = await db.get(
                RuntimeEventConsumer,
                {
                    "consumer_name": "agenthub_generation_projection",
                    "run_id": generation_id,
                },
            )
            before_sequence = consumer.last_sequence

        next_event = RuntimeEvent(
            type="agent.report",
            payload={
                "generation_id": generation_id,
                "runtime_run_id": generation_id,
                "runtime_event_id": "rollback-event",
                "runtime_sequence": before_sequence + 1,
                "agent_id": "agent",
                "work_product": "must roll back",
            },
        )
        with patch.object(
            manager,
            "_persist_agent_report_message",
            new=AsyncMock(side_effect=RuntimeError("projection failed")),
        ):
            with pytest.raises(RuntimeError, match="projection failed"):
                await manager._record_generation_event(
                    "conversation", generation_id, next_event
                )

        async with factory() as db:
            conversation = await db.get(Conversation, "conversation")
            generation = conversation.extra["runtime"]["generations"][-1]
            consumer = await db.get(
                RuntimeEventConsumer,
                {
                    "consumer_name": "agenthub_generation_projection",
                    "run_id": generation_id,
                },
            )
            assert generation.get("event_counts") == before_counts
            assert consumer.last_sequence == before_sequence
    finally:
        await engine.dispose()


async def test_generation_projection_catches_up_from_durable_journal(tmp_path):
    executor = StreamingExecutor()
    engine, factory, manager = await _prepare_manager(tmp_path, executor)
    try:
        with patch("app.events.WebSocketSink.emit", new=AsyncMock()):
            await manager.start_generation("conversation", "work")
            await manager._running_tasks["conversation"]
            await asyncio.sleep(0.05)

        async with factory() as db:
            conversation = await db.get(Conversation, "conversation")
            extra = deepcopy(conversation.extra)
            generation = extra["runtime"]["generations"][-1]
            generation_id = str(generation["id"])
            generation["event_counts"] = {}
            conversation.extra = extra
            await db.execute(
                delete(RuntimeEventConsumer).where(
                    RuntimeEventConsumer.run_id == generation_id
                )
            )
            await db.commit()

        journal = manager._bindings["conversation"].engine.run_journal
        page = await journal.read_events(generation_id)
        with patch("app.events.WebSocketSink.emit", new=AsyncMock()):
            cursor = await manager._catch_up_generation_projection(
                "conversation",
                generation_id,
                journal,
            )

        assert cursor == page.last_sequence
        async with factory() as db:
            conversation = await db.get(Conversation, "conversation")
            generation = conversation.extra["runtime"]["generations"][-1]
            consumer = await db.get(
                RuntimeEventConsumer,
                {
                    "consumer_name": "agenthub_generation_projection",
                    "run_id": generation_id,
                },
            )
            assert generation["event_counts"]
            assert consumer.last_sequence == page.last_sequence
    finally:
        await engine.dispose()
