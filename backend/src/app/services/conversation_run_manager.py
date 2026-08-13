"""Conversation-level Runtime Run manager.

The manager owns reusable RuntimeEngine adapters for each conversation,
starts and cancels generations, queues user inputs, and records runtime events
for recovery.
"""

from __future__ import annotations

import asyncio
from typing import Any, ClassVar, Optional

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agent_runtime import RunHandle, RunRequest, RunState
from agent_runtime.core.protocol import SCHEDULER_SUMMARY
from agent_runtime.core.types import Event as RuntimeEvent
from app.events import WebSocketSink
from app.persistence.runtime_journal import SQLRunJournal
from db.models import (
    Conversation,
    ConversationParticipant,
    Message,
    RuntimeEventConsumer,
    RuntimeRun,
    utcnow,
)
from db.session import AsyncSessionLocal
from app.services.runtime.generation_records import (
    fail_abandoned_generation_record,
    cancel_abandoned_generation_record,
    create_generation_record,
    finish_generation_record,
    record_generation_event,
    reconcile_terminal_run_records,
)
from app.services.serialization import conversation_to_dict, message_to_dict
from app.services.chat.scheduling import resolve_scheduling_strategy, workflow_enabled
from app.services.runtime.event_projection import project_runtime_event
from app.services.runtime_service import OrchestratorService, RuntimeBinding
from common.logger import get_logger

logger = get_logger("app.services.conversation_run_manager")


class RunManagerNotReadyError(Exception):
    """Raised when no RuntimeEngine binding is cached for a conversation."""

    pass


class RunAlreadyActiveError(Exception):
    """Raised when a conversation already has a running generation."""

    pass


class RuntimeProjectionGapError(RuntimeError):
    """Raised when a read-model consumer observes a non-contiguous sequence."""


class ConversationRunManager:
    """Conversation-level manager with at most one active RunHandle."""

    _instance: ClassVar[Optional["ConversationRunManager"]] = None

    def __init__(self, session_factory: Any = None):
        self._bindings: dict[str, RuntimeBinding] = {}
        self._active_handles: dict[str, RunHandle] = {}
        self._session_model_config_ids: dict[str, str | None] = {}
        self._session_scheduling_strategies: dict[str, str] = {}
        self._session_workflow_enabled: dict[str, bool] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._running_tasks: dict[str, asyncio.Task] = {}
        self._generation_ids: dict[str, str] = {}
        self._active_user_message_ids: dict[str, str] = {}
        self._queued_inputs: dict[str, list[dict[str, Any]]] = {}
        self._pending_preview_message_ids: dict[str, list[str]] = {}
        self._generation_thinking_enabled: dict[str, bool] = {}
        self._session_factory = session_factory or AsyncSessionLocal

    @classmethod
    def get_instance(cls) -> "ConversationRunManager":
        """Return the singleton manager."""
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _get_lock(self, conversation_id: str) -> asyncio.Lock:
        """Return the per-conversation lock."""
        if conversation_id not in self._locks:
            self._locks[conversation_id] = asyncio.Lock()
        return self._locks[conversation_id]

    async def get_or_create_engine(
        self,
        db: AsyncSession,
        conversation: Conversation,
        model_config_id: str | None = None,
        event_sink=None,
    ) -> RuntimeBinding:
        """Get or create cached adapters; terminated Runs themselves are never cached."""



        conversation_id = str(conversation.id)
        requested_model_config_id = str(model_config_id) if model_config_id else None
        requested_strategy = resolve_scheduling_strategy(conversation)
        requested_workflow_enabled = workflow_enabled(conversation)

        async with self._get_lock(conversation_id):
            await self._recover_abandoned_generation_if_needed(
                db,
                conversation,
                reason="process_lost",
            )

            if conversation_id in self._bindings:
                task = self._running_tasks.get(conversation_id)
                if task and not task.done():
                    return self._bindings[conversation_id]
                if (
                    self._session_model_config_ids.get(conversation_id) == requested_model_config_id
                    and self._session_scheduling_strategies.get(conversation_id) == requested_strategy
                    and self._session_workflow_enabled.get(conversation_id) == requested_workflow_enabled
                ):
                    return self._bindings[conversation_id]
                self._bindings.pop(conversation_id, None)
                self._session_model_config_ids.pop(conversation_id, None)
                self._session_scheduling_strategies.pop(conversation_id, None)
                self._session_workflow_enabled.pop(conversation_id, None)

            agents = await OrchestratorService._get_conversation_agents(db, conversation)
            if not agents:
                raise ValueError(f"Conversation has no available agents: conversation_id={conversation_id}")

            binding = await OrchestratorService.create_engine(
                db,
                conversation,
                agents,
                model_config_id,
                scheduling_strategy=requested_strategy,
                session_factory=self._session_factory,
            )
            self._bindings[conversation_id] = binding
            self._session_model_config_ids[conversation_id] = requested_model_config_id
            self._session_scheduling_strategies[conversation_id] = requested_strategy
            self._session_workflow_enabled[conversation_id] = requested_workflow_enabled

            conversation.generation_status = "idle"
            await db.commit()

            logger.info(
                "RuntimeEngine binding created",
                conversation_id=conversation_id,
                agent_count=len(agents),
            )
            return binding

    async def recover_conversation(self, conversation_id: str, *, reason: str = "process_lost") -> bool:
        """Recover a conversation whose running generation belongs to a dead process."""
        if self.is_generation_running(conversation_id):
            return False
        async with self._get_lock(conversation_id):
            if self.is_generation_running(conversation_id):
                return False
            recovered_message: Message | None = None
            async with self._session_factory() as db:
                conversation = await db.get(Conversation, conversation_id)
                if conversation is None:
                    return False
                generation_id = await self._recover_abandoned_generation_if_needed(
                    db, conversation, reason=reason
                )
                if not generation_id:
                    return False
                await db.refresh(conversation)
                generation = _runtime_generation(conversation, generation_id) or {}
                status = str(generation.get("status") or "failed")
                error = str(generation.get("error") or reason)
                if status in {"cancelled", "failed"}:
                    recovered_message = await self._persist_recovered_generation_notice(
                        db,
                        conversation_id,
                        generation_id,
                        status=status,
                        error=error,
                    )
            self._generation_ids.pop(conversation_id, None)
            self._pending_preview_message_ids.pop(generation_id, None)
            self._generation_thinking_enabled.pop(generation_id, None)
            self._active_user_message_ids.pop(conversation_id, None)
            self._queued_inputs.pop(conversation_id, None)
        await self._publish_conversation_snapshot(conversation_id)
        if recovered_message is not None:
            await WebSocketSink(conversation_id).emit(
                RuntimeEvent(
                    type="message:created",
                    payload=message_to_dict(recovered_message),
                )
            )
        event_type = "generation_finished" if status == "completed" else f"generation:{status}"
        await WebSocketSink(conversation_id).emit(
            RuntimeEvent(
                type=event_type,
                payload={
                    "conversation_id": conversation_id,
                    "generation_id": generation_id,
                    "status": status,
                    "error": error,
                },
            )
        )
        return True

    async def start_generation(
        self,
        conversation_id: str,
        content: str,
        *,
        runtime_content: str | None = None,
        thinking_enabled: bool = False,
        user_message_id: str | None = None,
        client_message_id: str | None = None,
        agent_mentions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Start a generation if this user message has not already been handled."""
        user_message_key = str(user_message_id or "").strip()
        async with self._get_lock(conversation_id):
            binding = self._bindings.get(conversation_id)
            if not binding:
                raise RunManagerNotReadyError(
                    f"Conversation {conversation_id} has no RuntimeEngine binding"
                )

            if user_message_key and await self._generation_exists_for_user_message(
                conversation_id,
                user_message_key,
            ):
                logger.info(
                    "Generation duplicate ignored",
                    conversation_id=conversation_id,
                    user_message_id=user_message_key,
                )
                return

            task = self._running_tasks.get(conversation_id)
            if task and not task.done():
                raise RunAlreadyActiveError(
                    f"Conversation {conversation_id} already has an active Run"
                )

            generation_id = await self._create_generation_record(
                conversation_id,
                binding,
                content,
                user_message_id=user_message_key or None,
            )
            await self._publish_conversation_snapshot(conversation_id)
            self._generation_thinking_enabled[generation_id] = bool(thinking_enabled)
            if user_message_key:
                self._active_user_message_ids[conversation_id] = user_message_key
            context_metadata = self._generation_context_metadata(
                conversation_id,
                content,
                user_message_id=user_message_id,
                client_message_id=client_message_id,
                agent_mentions=agent_mentions,
            )
            context_metadata["allowed_agent_ids"] = [agent.id for agent in binding.agents]
            context_metadata["mentioned_agent_ids"] = context_metadata.get(
                "mention_target_agent_ids", []
            )
            handle = await binding.engine.start(
                RunRequest(
                    run_id=generation_id,
                    context_scope_id=conversation_id,
                    input=runtime_content or content,
                    agents=binding.agents,
                    policy=binding.create_policy(),
                    metadata=context_metadata,
                )
            )
            self._active_handles[conversation_id] = handle
            task = asyncio.create_task(
                self._run_generation(
                    handle,
                    conversation_id,
                    generation_id,
                ),
                name=f"generation-{conversation_id}",
            )
            self._running_tasks[conversation_id] = task
            task.add_done_callback(
                lambda t, cid=conversation_id, gid=generation_id: self._on_generation_done(cid, gid, t)
            )

        logger.info("Generation started", conversation_id=conversation_id, content_preview=content[:50])


    async def _persist_recovered_generation_notice(
        self,
        db: AsyncSession,
        conversation_id: str,
        generation_id: str,
        *,
        status: str,
        error: str | None,
    ) -> Message | None:
        existing = await db.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.extra["runtime_generation_id"].as_string() == generation_id,
                Message.extra["runtime_recovery_notice"].as_boolean().is_(True),
                Message.deleted_at.is_(None),
            )
            .order_by(Message.created_at.desc())
        )
        if existing:
            return existing

        has_visible_agent_message = await db.scalar(
            select(Message.id)
            .where(
                Message.conversation_id == conversation_id,
                Message.sender_type == "agent",
                Message.extra["runtime_generation_id"].as_string() == generation_id,
                Message.deleted_at.is_(None),
            )
            .limit(1)
        )
        if has_visible_agent_message:
            return None

        reason = str(error or "server_restarted")
        reason_text = "服务重启" if reason == "server_restarted" else "运行中断"
        text = f"本次生成因{reason_text}已中断，未能完成输出。请重新发送上一条需求再试。"
        if status == "failed":
            text = f"本次生成失败：{reason[:160] or '运行异常'}。请重新发送上一条需求再试。"
        message = Message(
            conversation_id=conversation_id,
            sender_type="agent",
            sender_id="system",
            sender_name="System",
            sender_avatar_url=None,
            content_type="text",
            content={
                "text": text,
                "runtime_recovery_notice": True,
                "status": status,
                "error": reason,
            },
            status=status,
            extra={
                "runtime_generation_id": generation_id,
                "runtime_recovery_notice": True,
            },
        )
        conversation = await db.get(Conversation, conversation_id)
        if conversation:
            conversation.last_message_preview = text[:300]
            conversation.last_message_sender = "System"
            conversation.last_message_at = utcnow()
            conversation.message_count += 1
        db.add(message)
        await db.commit()
        await db.refresh(message)
        return message


    async def _run_generation(
        self,
        handle: RunHandle,
        conversation_id: str,
        generation_id: str,
    ) -> None:
        """Project one Run's envelopes onto the existing AgentHub read model."""

        async for envelope in handle.events():
            event = project_runtime_event(envelope)
            delay = 0.05
            while True:
                try:
                    applied = await self._record_generation_event(
                        conversation_id,
                        generation_id,
                        event,
                    )
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning(
                        "Runtime projection retry",
                        conversation_id=conversation_id,
                        generation_id=generation_id,
                        sequence=envelope.sequence,
                        error=str(exc),
                    )
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, 1.0)
            if applied:
                await WebSocketSink(conversation_id).emit(event)
        result = await handle.result()
        if result.state is RunState.CANCELLED:
            raise asyncio.CancelledError(result.reason_code)
        if result.state is RunState.FAILED:
            raise RuntimeError(result.reason_code)

    async def send_user_input(
        self,
        conversation_id: str,
        content: str,
        *,
        runtime_content: str | None = None,
        thinking_enabled: bool = False,
        user_message_id: str | None = None,
        client_message_id: str | None = None,
        agent_mentions: list[dict[str, Any]] | None = None,
    ) -> None:
        """Send user input to the active session with message-level idempotency."""
        user_message_key = str(user_message_id or "").strip()
        queued_payload: dict[str, str] | None = None
        async with self._get_lock(conversation_id):
            if conversation_id not in self._bindings:
                raise RunManagerNotReadyError(
                    f"Conversation {conversation_id} has no RuntimeEngine binding"
                )

            if user_message_key and await self._generation_exists_for_user_message(
                conversation_id,
                user_message_key,
            ):
                logger.info(
                    "User input duplicate ignored",
                    conversation_id=conversation_id,
                    user_message_id=user_message_key,
                )
                return

            task = self._running_tasks.get(conversation_id)
            if task and not task.done():
                logger.info("User input queued", conversation_id=conversation_id, content_preview=content[:50])
                self._queued_inputs.setdefault(conversation_id, []).append(
                    {
                        "content": content,
                        "runtime_content": runtime_content,
                        "thinking_enabled": bool(thinking_enabled),
                        "user_message_id": user_message_key or None,
                        "client_message_id": str(client_message_id or "") or None,
                        "agent_mentions": agent_mentions or [],
                    }
                )
                queued_payload = {
                    "conversation_id": conversation_id,
                    "content_preview": content[:80],
                }

        if queued_payload:
            await WebSocketSink(conversation_id).emit(
                RuntimeEvent(type="user.input_queued", payload=queued_payload)
            )
            return

        logger.info("User input starts generation", conversation_id=conversation_id, content_preview=content[:50])
        await self.start_generation(
            conversation_id,
            content,
            runtime_content=runtime_content,
            thinking_enabled=thinking_enabled,
            user_message_id=user_message_id,
            client_message_id=client_message_id,
            agent_mentions=agent_mentions,
        )


    async def cancel_generation(self, conversation_id: str) -> bool:
        """Cancel the active generation."""



        task = self._running_tasks.get(conversation_id)
        if task and not task.done():
            handle = self._active_handles.get(conversation_id)
            if handle:
                await handle.cancel("user_cancelled")
            logger.info("Generation cancellation requested", conversation_id=conversation_id)

            # Broadcast cancellation so all connected clients stop rendering the stream.
            cancel_event = RuntimeEvent(
                type="control.cancel",
                payload={"conversation_id": conversation_id, "reason": "user_cancelled"},
            )
            ws_sink = WebSocketSink(conversation_id)
            await ws_sink.emit(cancel_event)
            generation_id = self._generation_ids.pop(conversation_id, None)
            if generation_id:
                self._pending_preview_message_ids.pop(generation_id, None)
                self._generation_thinking_enabled.pop(generation_id, None)
                await self._finish_generation(
                    conversation_id,
                    generation_id,
                    status="cancelled",
                    error="user_cancelled",
                )
            self._queued_inputs.pop(conversation_id, None)
            self._active_user_message_ids.pop(conversation_id, None)

            return True
        binding = self._bindings.get(conversation_id)
        cancel_abandoned = (
            getattr(binding.engine.run_journal, "cancel_abandoned", None) if binding else None
        )
        if cancel_abandoned is None:
            cancel_abandoned = SQLRunJournal(self._session_factory).cancel_abandoned
        cancelled_ids = await cancel_abandoned(conversation_id)
        async with self._session_factory() as db:
            if cancelled_ids:
                reconciled = await reconcile_terminal_run_records(db, cancelled_ids)
                generation_id = (
                    reconciled[-1].generation_id if reconciled else cancelled_ids[-1]
                )
            else:
                generation_id = await cancel_abandoned_generation_record(
                    db, conversation_id, reason="user_cancelled"
                )
        return generation_id is not None

    async def _recover_abandoned_generation_if_needed(
        self,
        db: AsyncSession,
        conversation: Conversation,
        *,
        reason: str,
    ) -> str | None:
        conversation_id = str(conversation.id)
        if self.is_generation_running(conversation_id):
            return None
        journal = SQLRunJournal(self._session_factory)
        await self._catch_up_conversation_projections(conversation_id, journal)
        recovered_ids = await journal.recover_process_lost(conversation_id)
        active_run_id = str(conversation.active_run_id or "")
        if not recovered_ids and active_run_id:
            try:
                page = await journal.read_events(active_run_id, limit=1)
            except KeyError:
                page = None
            if page is not None and page.terminal:
                recovered_ids = [active_run_id]
        if recovered_ids:
            for run_id in recovered_ids:
                await self._catch_up_generation_projection(
                    conversation_id,
                    run_id,
                    journal,
                )
            await db.refresh(conversation)
            recovered = await reconcile_terminal_run_records(db, recovered_ids)
            generation_id = recovered[-1].generation_id if recovered else recovered_ids[-1]
        else:
            generation_id = await fail_abandoned_generation_record(
                db,
                conversation_id,
                reason=reason,
            )
        if not generation_id:
            return None
        await db.refresh(conversation)
        self._generation_ids.pop(conversation_id, None)
        self._pending_preview_message_ids.pop(generation_id, None)
        self._generation_thinking_enabled.pop(generation_id, None)
        self._active_user_message_ids.pop(conversation_id, None)
        self._queued_inputs.pop(conversation_id, None)
        logger.info(
            "Recovered abandoned generation",
            conversation_id=conversation_id,
            generation_id=generation_id,
            reason=reason,
        )
        return generation_id

    def _on_generation_done(self, conversation_id: str, generation_id: str, task: asyncio.Task) -> None:
        """Handle generation task completion."""
        self._running_tasks.pop(conversation_id, None)
        handle = self._active_handles.get(conversation_id)
        if handle and handle.run_id == generation_id:
            self._active_handles.pop(conversation_id, None)

        try:
            task.result()
            logger.info("Generation completed", conversation_id=conversation_id)
            status = "completed"
            error = None
        except asyncio.CancelledError:
            logger.info("Generation cancelled", conversation_id=conversation_id)
            status = "cancelled"
            error = "cancelled"
        except Exception as e:
            logger.error("Generation failed", conversation_id=conversation_id, error=str(e))
            status = "failed"
            error = str(e)

        if self._generation_ids.get(conversation_id) != generation_id:
            return
        self._generation_ids.pop(conversation_id, None)
        self._active_user_message_ids.pop(conversation_id, None)
        next_input = self._dequeue_next_input(conversation_id)
        try:
            asyncio.create_task(
                self._finish_generation_and_continue(
                    conversation_id,
                    generation_id,
                    status=status,
                    error=error,
                    next_input=next_input,
                )
            )
        except RuntimeError:
            logger.warning("Generation finalization task failed to start", conversation_id=conversation_id)

    async def close_conversation(self, conversation_id: str) -> None:
        """Cancel active work and forget cached adapters for a conversation."""



        await self.cancel_generation(conversation_id)
        binding = self._bindings.pop(conversation_id, None)
        self._active_handles.pop(conversation_id, None)
        self._session_model_config_ids.pop(conversation_id, None)
        self._session_scheduling_strategies.pop(conversation_id, None)
        self._session_workflow_enabled.pop(conversation_id, None)
        generation_id = self._generation_ids.pop(conversation_id, None)
        if generation_id:
            self._pending_preview_message_ids.pop(generation_id, None)
            self._generation_thinking_enabled.pop(generation_id, None)
        self._queued_inputs.pop(conversation_id, None)
        self._active_user_message_ids.pop(conversation_id, None)
        self._locks.pop(conversation_id, None)

        if binding:
            logger.info("RuntimeEngine binding closed", conversation_id=conversation_id)

    def get_run_status(self, conversation_id: str) -> dict | None:
        """Return the active Run snapshot, if any."""
        handle = self._active_handles.get(conversation_id)
        if not handle:
            return None
        snapshot = handle.snapshot()
        return {
            "run_id": snapshot.run_id,
            "status": snapshot.state.value,
            "reason_code": snapshot.reason_code,
            "sequence": snapshot.sequence,
            "usage": snapshot.usage.to_dict(),
        }

    def is_generation_running(self, conversation_id: str) -> bool:
        """Return whether a generation is currently running."""
        task = self._running_tasks.get(conversation_id)
        return task is not None and not task.done()

    @staticmethod
    def _generation_context_metadata(
        conversation_id: str,
        content: str,
        *,
        user_message_id: str | None,
        client_message_id: str | None = None,
        agent_mentions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        metadata = {
            "conversation_id": str(conversation_id),
            "session_id": str(conversation_id),
            "visible_content": str(content or ""),
        }
        if user_message_id:
            metadata["user_message_id"] = str(user_message_id)
        if client_message_id:
            metadata["client_message_id"] = str(client_message_id)
        mention_ids = _mention_target_agent_ids(agent_mentions)
        if mention_ids:
            metadata["mention_target_agent_ids"] = mention_ids
            metadata["agent_mentions"] = agent_mentions or []
        return metadata

    async def _generation_exists_for_user_message(
        self,
        conversation_id: str,
        user_message_id: str,
    ) -> bool:
        active_user_message_id = self._active_user_message_ids.get(conversation_id)
        if active_user_message_id == user_message_id:
            return True
        if self._queued_user_message_exists(conversation_id, user_message_id):
            return True

        async with self._session_factory() as db:
            conversation = await db.get(Conversation, conversation_id)
            if not conversation:
                return False
            runtime = (conversation.extra or {}).get("runtime") or {}
            for item in runtime.get("generations") or []:
                if str(item.get("user_message_id") or "") == user_message_id:
                    return True
        return False

    def _queued_user_message_exists(self, conversation_id: str, user_message_id: str) -> bool:
        return any(
            str(item.get("user_message_id") or "") == user_message_id
            for item in self._queued_inputs.get(conversation_id, [])
        )

    async def _create_generation_record(
        self,
        conversation_id: str,
        binding: RuntimeBinding,
        content: str,
        *,
        user_message_id: str | None = None,
    ) -> str:
        async with self._session_factory() as db:
            generation_id = await create_generation_record(
                db,
                conversation_id,
                run_id=None,
                agents=binding.agents,
                prompt=content,
                user_message_id=user_message_id,
                model_config_id=self._session_model_config_ids.get(conversation_id),
                scheduling_strategy=self._session_scheduling_strategies.get(conversation_id),
                workflow_enabled=self._session_workflow_enabled.get(conversation_id),
            )
        self._generation_ids[conversation_id] = generation_id
        return generation_id

    def _dequeue_next_input(self, conversation_id: str) -> dict[str, Any] | None:
        queue = self._queued_inputs.get(conversation_id)
        if not queue:
            return None
        next_input = queue.pop(0)
        if not queue:
            self._queued_inputs.pop(conversation_id, None)
        return next_input

    async def _record_generation_event(
        self,
        conversation_id: str,
        generation_id: str,
        event: RuntimeEvent,
    ) -> bool:
        self._collect_preview_message_id(generation_id, event)
        payload = event.payload if isinstance(event.payload, dict) else {}
        event_id = str(payload.get("runtime_event_id") or "")
        sequence = int(payload.get("runtime_sequence") or 0)
        consumer_name = "agenthub_generation_projection"
        message = None
        async with self._session_factory() as db:
            async with db.begin():
                consumer = None
                if sequence:
                    consumer = await db.scalar(
                        select(RuntimeEventConsumer)
                        .where(
                            RuntimeEventConsumer.consumer_name == consumer_name,
                            RuntimeEventConsumer.run_id == generation_id,
                        )
                        .with_for_update()
                    )
                    last_sequence = consumer.last_sequence if consumer is not None else 0
                    if sequence <= last_sequence:
                        return False
                    if sequence != last_sequence + 1:
                        raise RuntimeProjectionGapError(
                            f"Run {generation_id} expected projection sequence "
                            f"{last_sequence + 1}, got {sequence}"
                        )
                await record_generation_event(db, conversation_id, generation_id, event)
                message = await self._persist_agent_report_message(
                    db,
                    conversation_id,
                    generation_id,
                    event,
                )
                if message is None:
                    message = await self._persist_scheduler_summary_message(
                        db,
                        conversation_id,
                        generation_id,
                        event,
                    )
                if sequence:
                    if consumer is None:
                        consumer = RuntimeEventConsumer(
                            consumer_name=consumer_name,
                            run_id=generation_id,
                        )
                        db.add(consumer)
                    consumer.last_sequence = sequence
                    consumer.last_event_id = event_id or None
        if message:
            sink = WebSocketSink(conversation_id)
            event_type = (
                "message:updated"
                if event.type == "system.agent_completed" or bool(getattr(message, "_runtime_emit_updated", False))
                else "message:new"
            )
            await sink.emit(RuntimeEvent(type=event_type, payload=message_to_dict(message)))
            await self._publish_pending_preview_messages(sink, conversation_id, generation_id)
        if _should_publish_conversation_snapshot(event.type):
            await self._publish_conversation_snapshot(conversation_id)
        if event.type == "control.watchdog_triggered":
            reason = str((event.payload or {}).get("reason") or "watchdog_triggered")
            if self._generation_ids.get(conversation_id) == generation_id:
                self._generation_ids.pop(conversation_id, None)
                self._pending_preview_message_ids.pop(generation_id, None)
                self._generation_thinking_enabled.pop(generation_id, None)
                await self._finish_generation(
                    conversation_id,
                    generation_id,
                    status="failed",
                    error=reason,
                )
        return True

    async def _catch_up_generation_projection(
        self,
        conversation_id: str,
        generation_id: str,
        journal: SQLRunJournal | None = None,
    ) -> int:
        """Replay durable events after the consumer's committed sequence."""

        async with self._session_factory() as db:
            consumer = await db.get(
                RuntimeEventConsumer,
                {
                    "consumer_name": "agenthub_generation_projection",
                    "run_id": generation_id,
                },
            )
            cursor = consumer.last_sequence if consumer is not None else 0
        store = journal or SQLRunJournal(self._session_factory)
        while True:
            page = await store.read_events(
                generation_id,
                after_sequence=cursor,
                limit=200,
            )
            for envelope in page.items:
                await self._record_generation_event(
                    conversation_id,
                    generation_id,
                    project_runtime_event(envelope, replayed=True),
                )
                cursor = envelope.sequence
            if cursor >= page.last_sequence:
                return cursor

    async def _catch_up_conversation_projections(
        self,
        conversation_id: str,
        journal: SQLRunJournal | None = None,
    ) -> None:
        """Repair durable generation projections before recovery decisions."""

        async with self._session_factory() as db:
            run_ids = tuple(
                (
                    await db.scalars(
                        select(RuntimeRun.id)
                        .where(RuntimeRun.context_scope_id == conversation_id)
                        .order_by(RuntimeRun.started_at.desc(), RuntimeRun.id.desc())
                        .limit(20)
                    )
                ).all()
            )
        store = journal or SQLRunJournal(self._session_factory)
        for run_id in reversed(run_ids):
            page = await store.read_events(run_id, after_sequence=0, limit=1)
            if page.history_complete and page.last_sequence:
                await self._catch_up_generation_projection(
                    conversation_id,
                    run_id,
                    store,
                )

    def _collect_preview_message_id(self, generation_id: str, event: RuntimeEvent) -> None:
        if event.type != "agent.tool_result":
            return
        payload = event.payload or {}
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            return
        output = result.get("output") if isinstance(result.get("output"), dict) else result
        if not isinstance(output, dict):
            return
        preview_id = str(output.get("preview_message_id") or "")
        if not preview_id:
            return
        pending = self._pending_preview_message_ids.setdefault(generation_id, [])
        if preview_id not in pending:
            pending.append(preview_id)

    async def _publish_pending_preview_messages(
        self,
        sink: WebSocketSink,
        conversation_id: str,
        generation_id: str,
    ) -> None:
        preview_ids = self._pending_preview_message_ids.pop(generation_id, [])
        if not preview_ids:
            return
        async with self._session_factory() as db:
            for preview_id in preview_ids:
                preview = await db.get(Message, preview_id)
                if (
                    not preview
                    or str(preview.conversation_id) != conversation_id
                    or preview.content_type != "preview_card"
                    or preview.deleted_at is not None
                ):
                    continue
                preview.created_at = utcnow()
                preview.updated_at = utcnow()
                await db.commit()
                await db.refresh(preview)
                await sink.emit(RuntimeEvent(type="message:new", payload=message_to_dict(preview)))

    async def _publish_conversation_snapshot(self, conversation_id: str) -> None:
        async with self._session_factory() as db:
            conversation = await db.scalar(
                select(Conversation)
                .where(Conversation.id == conversation_id)
                .options(
                    selectinload(Conversation.participants).selectinload(
                        ConversationParticipant.agent
                    )
                )
            )
            if not conversation:
                return
            payload = conversation_to_dict(conversation)
        await WebSocketSink(conversation_id).emit(
            RuntimeEvent(type="conversation:updated", payload=payload)
        )

    async def _finish_generation_and_continue(
        self,
        conversation_id: str,
        generation_id: str,
        *,
        status: str,
        error: str | None,
        next_input: dict[str, Any] | None,
    ) -> None:
        await self._finish_generation(
            conversation_id,
            generation_id,
            status=status,
            error=error,
        )
        if not next_input:
            return

        next_content = str(next_input.get("content") or "").strip()
        next_runtime_content = next_input.get("runtime_content")
        next_thinking_enabled = bool(next_input.get("thinking_enabled"))
        next_user_message_id = next_input.get("user_message_id")
        next_client_message_id = next_input.get("client_message_id")
        next_agent_mentions = next_input.get("agent_mentions")
        if not next_content:
            return

        try:
            await self.start_generation(
                conversation_id,
                next_content,
                runtime_content=next_runtime_content,
                thinking_enabled=next_thinking_enabled,
                user_message_id=str(next_user_message_id) if next_user_message_id else None,
                client_message_id=str(next_client_message_id) if next_client_message_id else None,
                agent_mentions=next_agent_mentions if isinstance(next_agent_mentions, list) else None,
            )
        except Exception as exc:
            logger.error("Queued input failed to start", conversation_id=conversation_id, error=str(exc))
            await WebSocketSink(conversation_id).emit(
                RuntimeEvent(
                    type="generation:failed",
                    payload={
                        "conversation_id": conversation_id,
                        "error": str(exc),
                    },
                )
            )

    async def _find_runtime_agent_message(
        self,
        db: AsyncSession,
        conversation_id: str,
        generation_id: str,
        agent_id: str,
        *,
        stream_message_id: str,
        task: str,
        work_product: str,
    ) -> Message | None:
        base_conditions = (
            Message.conversation_id == conversation_id,
            Message.sender_type == "agent",
            Message.sender_id == (agent_id or None),
            Message.extra["runtime_generation_id"].as_string() == generation_id,
            Message.deleted_at.is_(None),
        )
        if stream_message_id:
            return await db.scalar(
                select(Message)
                .where(
                    *base_conditions,
                    or_(
                        Message.content["agent_message_id"].as_string() == stream_message_id,
                        Message.content["message_id"].as_string() == stream_message_id,
                        Message.content["stream_message_id"].as_string() == stream_message_id,
                    ),
                )
                .order_by(Message.created_at.desc())
            )
        if task:
            existing = await db.scalar(
                select(Message)
                .where(
                    *base_conditions,
                    Message.extra["runtime_report_task"].as_string() == task,
                )
                .order_by(Message.created_at.desc())
            )
            if existing:
                return existing
        if work_product:
            candidates = (
                await db.scalars(
                    select(Message)
                    .where(*base_conditions)
                    .order_by(Message.created_at.desc())
                    .limit(20)
                )
            ).all()
            return next(
                (
                    message
                    for message in candidates
                    if str((message.content or {}).get("text") or "") == work_product
                ),
                None,
            )
        return None

    async def _persist_agent_report_message(
        self,
        db: AsyncSession,
        conversation_id: str,
        generation_id: str,
        event: RuntimeEvent,
    ) -> Message | None:
        if event.type not in {"agent.report", "system.agent_completed"}:
            return None
        payload = event.payload or {}
        work_product = str(payload.get("work_product") or "").strip()
        if not work_product:
            return None
        thinking_enabled = self._generation_thinking_enabled.get(generation_id, False)

        agent_id = str(payload.get("agent_id") or "")
        stream_message_id = str(
            payload.get("stream_message_id")
            or payload.get("agent_message_id")
            or payload.get("message_id")
            or ""
        )
        binding = self._bindings.get(conversation_id)
        agent = (
            next((item for item in binding.agents if item.id == agent_id), None)
            if binding and agent_id
            else None
        )
        agent_name = getattr(agent, "name", None) or str(payload.get("agent_name") or "Agent")
        agent_avatar_url = (
            str((getattr(agent, "model_config", {}) or {}).get("avatar_url") or "")
            or str(payload.get("agent_avatar_url") or payload.get("sender_avatar_url") or "")
            or None
        )
        task = str(payload.get("task") or "")
        existing = await self._find_runtime_agent_message(
            db,
            conversation_id,
            generation_id,
            agent_id,
            stream_message_id=stream_message_id,
            task=task,
            work_product=work_product,
        )
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        status_report = (
            payload.get("status_report")
            if isinstance(payload.get("status_report"), dict)
            else {}
        )
        runtime_report = report or status_report
        existing_content = existing.content if existing and isinstance(existing.content, dict) else {}
        existing_content = {
            key: value for key, value in existing_content.items() if key != "thinking"
        }
        content = {
            "text": work_product,
            "thinking_enabled": thinking_enabled,
            "runtime_report": runtime_report or existing_content.get("runtime_report") or {},
        }
        if agent_id:
            content["agent_id"] = agent_id
        if stream_message_id:
            content["agent_message_id"] = stream_message_id
            content["message_id"] = stream_message_id
            content["stream_message_id"] = stream_message_id

        if existing:
            existing.content = {**existing_content, **content}
            existing.sender_name = existing.sender_name or agent_name
            if agent_avatar_url and not existing.sender_avatar_url:
                existing.sender_avatar_url = agent_avatar_url
            existing.status = "completed"
            existing.updated_at = utcnow()
            existing.extra = {
                **(existing.extra or {}),
                "runtime_generation_id": generation_id,
                "runtime_agent_report": True,
                "runtime_report_task": task,
            }
            conversation = await db.get(Conversation, conversation_id)
            if conversation:
                conversation.last_message_preview = work_product[:300]
                conversation.last_message_sender = existing.sender_name or agent_name
                conversation.last_message_at = utcnow()
            await db.flush()
            await db.refresh(existing)
            return existing if event.type == "system.agent_completed" else None

        message = Message(
            conversation_id=conversation_id,
            sender_type="agent",
            sender_id=agent_id or None,
            sender_name=agent_name,
            sender_avatar_url=agent_avatar_url,
            content_type="text",
            content=content,
            status="completed",
            extra={
                "runtime_generation_id": generation_id,
                "runtime_agent_report": True,
                "runtime_report_task": task,
            },
        )
        conversation = await db.get(Conversation, conversation_id)
        if conversation:
            conversation.last_message_preview = work_product[:300]
            conversation.last_message_sender = agent_name
            conversation.last_message_at = utcnow()
            conversation.message_count += 1
        db.add(message)
        await db.flush()
        await db.refresh(message)
        return message

    async def _persist_scheduler_summary_message(
        self,
        db: AsyncSession,
        conversation_id: str,
        generation_id: str,
        event: RuntimeEvent,
    ) -> Message | None:
        if event.type != SCHEDULER_SUMMARY:
            return None
        payload = event.payload if isinstance(event.payload, dict) else {}
        if payload.get("publish_message") is False:
            return None
        final_answer = str(payload.get("final_answer") or "").strip()
        if not final_answer:
            return None

        existing = await db.scalar(
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                Message.sender_type == "agent",
                Message.sender_id == "team_leader",
                Message.extra["runtime_generation_id"].as_string() == generation_id,
                Message.extra["runtime_scheduler_summary"].as_boolean().is_(True),
                Message.deleted_at.is_(None),
            )
            .order_by(Message.created_at.desc())
        )
        content = {
            "text": final_answer,
            "agent_id": "team_leader",
            "runtime_report": payload,
            "runtime_summary": payload,
            "final_product": payload.get("final_product"),
            "final_deliverable": payload.get("final_deliverable"),
            "compliance_checks": payload.get("compliance_checks") or [],
            "logic_chain": payload.get("logic_chain") or [],
            "thinking_enabled": False,
        }
        conversation = await db.get(Conversation, conversation_id)
        if existing:
            existing.content = {
                **(existing.content if isinstance(existing.content, dict) else {}),
                **content,
            }
            existing.sender_name = existing.sender_name or "Team Leader"
            existing.status = "completed"
            existing.updated_at = utcnow()
            existing.extra = {
                **(existing.extra or {}),
                "runtime_generation_id": generation_id,
                "runtime_scheduler_summary": True,
            }
            if conversation:
                conversation.last_message_preview = final_answer[:300]
                conversation.last_message_sender = existing.sender_name or "Team Leader"
                conversation.last_message_at = utcnow()
            await db.flush()
            await db.refresh(existing)
            setattr(existing, "_runtime_emit_updated", True)
            return existing

        message = Message(
            conversation_id=conversation_id,
            sender_type="agent",
            sender_id="team_leader",
            sender_name="Team Leader",
            sender_avatar_url=None,
            content_type="text",
            content=content,
            status="completed",
            extra={
                "runtime_generation_id": generation_id,
                "runtime_scheduler_summary": True,
            },
        )
        if conversation:
            conversation.last_message_preview = final_answer[:300]
            conversation.last_message_sender = "Team Leader"
            conversation.last_message_at = utcnow()
            conversation.message_count += 1
        db.add(message)
        await db.flush()
        await db.refresh(message)
        return message

    async def _finish_generation(
        self,
        conversation_id: str,
        generation_id: str,
        *,
        status: str,
        error: str | None = None,
    ) -> None:
        async with self._session_factory() as db:
            await finish_generation_record(
                db,
                conversation_id,
                generation_id,
                status=status,
                error=error,
            )
        await self._publish_conversation_snapshot(conversation_id)
        event_type = {
            "cancelled": "generation:cancelled",
            "failed": "generation:failed",
        }.get(status, "generation_finished")
        await WebSocketSink(conversation_id).emit(
            RuntimeEvent(
                type=event_type,
                payload={
                    "conversation_id": conversation_id,
                    "generation_id": generation_id,
                    "status": status,
                    "error": error,
                },
            )
        )
        self._generation_thinking_enabled.pop(generation_id, None)


def _should_publish_conversation_snapshot(event_type: str) -> bool:
    if event_type in {"agent.token", "agent.thinking", "content_block_delta"}:
        return False
    return event_type.startswith(("system.", "scheduler.", "control.", "user.")) or event_type in {
        "agent.state_changed",
        "agent.report",
        "agent.failed",
        "agent.tool_call",
        "agent.tool_result",
        "blackboard.updated",
    }


def _mention_target_agent_ids(agent_mentions: list[dict[str, Any]] | None) -> list[str]:
    ids: list[str] = []
    for item in agent_mentions or []:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("agent_id") or item.get("id") or "").strip()
        if agent_id and agent_id not in ids:
            ids.append(agent_id)
    return ids


def _runtime_generation(conversation: Conversation, generation_id: str) -> dict[str, Any] | None:
    runtime = (conversation.extra or {}).get("runtime") if isinstance(conversation.extra, dict) else {}
    for item in (runtime or {}).get("generations") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == generation_id:
            return item
    return None
