"""RuntimeEngine, RunHandle, and the single-owner RunKernel."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from dataclasses import replace
from typing import Any

from model_provider.core.streaming import OutputTokenLimitExceeded

from ..context.scope_store import InMemoryContextStore, VersionedBlackboard
from ..core.ports import (
    AgentExecutor,
    ContextConflictError,
    ContextStore,
    RunEventSink,
    RunJournal,
    TeamJournal,
)
from ..core.run_types import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentMemory,
    CollaborationSnapshot,
    ContextDelta,
    ContextSnapshot,
    EventEnvelope,
    PolicySnapshot,
    RunRequest,
    RunResult,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    TeamMessage,
    Usage,
    utc_now,
)
from .cancellation import CancellationScope, RunLease
from .adapter_isolation import AdapterNotCancellableError, AdapterTimeoutError
from .agent_actor import AgentActor
from .run_journal import EventJournalError, EventSequenceConflictError, InMemoryRunJournal
from .run_watchdog import RunWatchdog
from .team_collaboration import (
    AgentTurnBudgetExceeded,
    CollaborationMessageBudgetExceeded,
    CollaborationProtocolError,
    InMemoryTeamJournal,
)


class RuntimeEngine:
    """Reusable dependency container that starts isolated one-shot runs."""

    def __init__(
        self,
        *,
        agent_executor: AgentExecutor,
        context_store: ContextStore | None = None,
        run_journal: RunJournal | None = None,
        team_journal: TeamJournal | None = None,
        event_sink: RunEventSink | None = None,
        limits: RuntimeLimits | None = None,
    ) -> None:
        self.agent_executor = agent_executor
        self.context_store = context_store or InMemoryContextStore()
        self.run_journal = run_journal or InMemoryRunJournal()
        self.team_journal = team_journal
        if self.team_journal is None and isinstance(self.run_journal, InMemoryRunJournal):
            self.team_journal = InMemoryTeamJournal(self.run_journal)
        self.event_sink = event_sink
        self.limits = limits or RuntimeLimits()
        self._active: dict[str, RunKernel] = {}
        self._closed = False

    @property
    def active_run_count(self) -> int:
        return len(self._active)

    async def start(self, request: RunRequest) -> "RunHandle":
        if self._closed:
            raise RuntimeError("RuntimeEngine has been shut down")
        if request.run_id in self._active:
            raise ValueError(f"Run is already active: {request.run_id}")
        kernel = RunKernel(
            request=request,
            agent_executor=self.agent_executor,
            context_store=self.context_store,
            run_journal=self.run_journal,
            team_journal=self.team_journal,
            event_sink=self.event_sink,
            limits=self.limits,
            on_terminal=self._active.pop,
        )
        self._active[request.run_id] = kernel
        kernel.start()
        return RunHandle(kernel)

    async def shutdown(self) -> tuple[RunResult, ...]:
        """Fail every active Run and reject future starts."""

        self._closed = True
        kernels = tuple(self._active.values())
        if not kernels:
            return ()
        results = await asyncio.gather(
            *(kernel.fail("runtime_shutdown") for kernel in kernels)
        )
        return tuple(results)


class RunHandle:
    def __init__(self, kernel: "RunKernel") -> None:
        self._kernel = kernel

    @property
    def run_id(self) -> str:
        return self._kernel.request.run_id

    def events(self, *, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]:
        return self._kernel.events(after_sequence=after_sequence)

    async def result(self) -> RunResult:
        return await asyncio.shield(self._kernel.result_future)

    async def cancel(self, reason: str = "user_cancelled") -> RunResult:
        return await self._kernel.cancel(reason)

    async def post_message(
        self, content: str, *, target_agent_ids: tuple[str, ...] = ()
    ) -> TeamMessage:
        return await self._kernel.post_message(content, target_agent_ids=target_agent_ids)

    def snapshot(self) -> RunSnapshot:
        return self._kernel.snapshot()


class RunKernel:
    """Owns every mutable transition for exactly one Run."""

    def __init__(
        self,
        *,
        request: RunRequest,
        agent_executor: AgentExecutor,
        context_store: ContextStore,
        run_journal: RunJournal,
        team_journal: TeamJournal | None,
        event_sink: RunEventSink | None,
        limits: RuntimeLimits,
        on_terminal=None,
    ) -> None:
        self.request = request
        self.agent_executor = agent_executor
        self.context_store = context_store
        self.run_journal = run_journal
        self.team_journal = team_journal
        self.event_sink = event_sink
        self.limits = limits
        self.state = RunState.CREATED
        self.reason_code: str | None = None
        self.started_at = None
        self.finished_at = None
        self.sequence = 0
        self.decision_count = 0
        self.no_progress_count = 0
        self.usage = Usage()
        self.cancellation = CancellationScope()
        self.lease = RunLease(request.run_id)
        self._event_condition = asyncio.Condition()
        self._journal_ready = asyncio.Event()
        self._journal_created = False
        self._journal_failed = False
        self._live_events: dict[int, EventEnvelope] = {}
        self._finish_lock = asyncio.Lock()
        self._sequence_lock = asyncio.Lock()
        self._managed_tasks: set[asyncio.Task] = set()
        self._actors: dict[str, AgentActor] = {}
        self._main_task: asyncio.Task | None = None
        self._watchdog_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._result_future: asyncio.Future[RunResult] | None = None
        self._context: ContextSnapshot | None = None
        self._blackboard: VersionedBlackboard | None = None
        self._memories: dict[str, AgentMemory] = {}
        self._reports = []
        self._outputs: list[str] = []
        self._last_event: EventEnvelope | None = None
        self._forced_failure_reason: str | None = None
        self._collaboration_enabled = bool(request.metadata.get("collaboration_enabled"))
        self._team_messages: dict[str, TeamMessage] = {}
        self._team_unread: dict[str, list[str]] = {
            agent.id: [] for agent in request.agents
        }
        self._team_open_threads: set[str] = set()
        self._agent_turn_counts: dict[str, int] = {
            agent.id: 0 for agent in request.agents
        }
        self._collaboration_message_count = 0
        self._collaboration_failure_reason: str | None = None
        self._summary_agent_id = str(request.metadata.get("summary_agent_id") or "") or None
        self._summary_scheduled = False
        self._summary_completed = False
        self._summary_output = ""
        self._accepting_team_messages = self._collaboration_enabled
        self._on_terminal = on_terminal
        self._watchdog = RunWatchdog(limits, self._fail_from_watchdog)

    @property
    def result_future(self) -> asyncio.Future[RunResult]:
        if self._result_future is None:
            loop = self._loop or asyncio.get_running_loop()
            self._result_future = loop.create_future()
        return self._result_future

    def start(self) -> None:
        if self._main_task is not None:
            raise RuntimeError("RunKernel has already started")
        self._loop = asyncio.get_running_loop()
        self._result_future = self._loop.create_future()
        self._main_task = self._loop.create_task(
            self._run(), name=f"runtime-run:{self.request.run_id}"
        )

    async def events(self, *, after_sequence: int = 0) -> AsyncIterator[EventEnvelope]:
        if after_sequence < 0:
            raise ValueError("after_sequence must be non-negative")
        await self._journal_ready.wait()
        if not self._journal_created:
            return
        cursor = after_sequence
        while True:
            async with self._event_condition:
                page = await self.run_journal.read_events(
                    self.request.run_id,
                    after_sequence=cursor,
                    limit=200,
                )
                if not page.items and not page.terminal and not self._journal_failed:
                    await self._event_condition.wait()
                    continue
            for event in page.items:
                cursor = event.sequence
                yield self._live_events.get(event.sequence, event)
            if page.terminal and cursor >= page.last_sequence:
                break
            if self._journal_failed and cursor >= page.last_sequence:
                break

    def snapshot(self) -> RunSnapshot:
        return RunSnapshot(
            run_id=self.request.run_id,
            context_scope_id=self.request.context_scope_id,
            state=self.state,
            reason_code=self.reason_code,
            sequence=self.sequence,
            decision_count=self.decision_count,
            no_progress_count=self.no_progress_count,
            usage=Usage(
                prompt_tokens=self.usage.prompt_tokens,
                completion_tokens=self.usage.completion_tokens,
                estimated=self.usage.estimated,
            ),
            context_version=self._context.version if self._context is not None else 0,
            limits=self.limits,
            started_at=self.started_at,
            finished_at=self.finished_at,
        )

    async def post_message(
        self, content: str, *, target_agent_ids: tuple[str, ...] = ()
    ) -> TeamMessage:
        """Inject user input into this Run for delivery at the next safe checkpoint."""

        try:
            return await self._send_team_message(
                sender_type="user",
                sender_id="user",
                content=content,
                recipient_agent_ids=target_agent_ids,
                expects_reply=False,
            )
        except CollaborationProtocolError as exc:
            await self._record_collaboration_rejection("user:user", str(exc))
            raise

    async def send_message(
        self,
        *,
        sender_agent_id: str,
        content: str,
        recipient_agent_ids: tuple[str, ...] = (),
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
        expects_reply: bool = False,
    ) -> TeamMessage:
        """TeamMessenger implementation exposed only to the executing Agent."""

        try:
            return await self._send_team_message(
                sender_type="agent",
                sender_id=sender_agent_id,
                content=content,
                recipient_agent_ids=recipient_agent_ids,
                thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
                expects_reply=expects_reply,
            )
        except CollaborationMessageBudgetExceeded:
            self._collaboration_failure_reason = "collaboration_message_budget_exhausted"
            await self._record_collaboration_rejection(
                f"agent:{sender_agent_id}", "collaboration message budget exhausted"
            )
            raise
        except CollaborationProtocolError as exc:
            self._collaboration_failure_reason = "collaboration_protocol_error"
            await self._record_collaboration_rejection(f"agent:{sender_agent_id}", str(exc))
            raise

    async def resolve_thread(
        self, *, agent_id: str, thread_id: str, conclusion: str
    ) -> None:
        if not self._collaboration_enabled or self.team_journal is None:
            raise CollaborationProtocolError("Team collaboration is not enabled")
        if agent_id not in self._team_unread:
            self._collaboration_failure_reason = "collaboration_protocol_error"
            raise CollaborationProtocolError(f"Unknown team agent: {agent_id}")
        if not thread_id or not conclusion.strip():
            self._collaboration_failure_reason = "collaboration_protocol_error"
            raise CollaborationProtocolError("thread_id and conclusion are required")
        async with self._sequence_lock:
            if thread_id not in self._team_open_threads:
                self._collaboration_failure_reason = "collaboration_protocol_error"
                raise CollaborationProtocolError(f"Thread is not open: {thread_id}")
            event = EventEnvelope(
                run_id=self.request.run_id,
                context_scope_id=self.request.context_scope_id,
                sequence=self.sequence + 1,
                type="collaboration.thread_resolved",
                source=f"agent:{agent_id}",
                payload={"thread_id": thread_id, "conclusion": conclusion.strip()},
            )
            try:
                persisted_event = await self.team_journal.resolve_thread(
                    thread_id, agent_id, event
                )
            except CollaborationProtocolError:
                raise
            except Exception as exc:
                self._collaboration_failure_reason = "event_store_error"
                raise EventJournalError("Failed to resolve collaboration thread") from exc
            self._team_open_threads.discard(thread_id)
            for message_id, message in tuple(self._team_messages.items()):
                if message.thread_id == thread_id:
                    self._team_messages[message_id] = replace(
                        message,
                        status="resolved",
                        resolved_at=persisted_event.occurred_at,
                    )
            await self._accept_committed_team_event(persisted_event)
        await self._publish_committed_event(persisted_event)

    async def _send_team_message(
        self,
        *,
        sender_type: str,
        sender_id: str,
        content: str,
        recipient_agent_ids: tuple[str, ...],
        thread_id: str | None = None,
        reply_to_message_id: str | None = None,
        expects_reply: bool = False,
    ) -> TeamMessage:
        if not self._collaboration_enabled or self.team_journal is None:
            raise CollaborationProtocolError("Team collaboration is not enabled")
        if self.state is not RunState.RUNNING or not self._accepting_team_messages:
            raise CollaborationProtocolError("Run is not accepting collaboration messages")
        text = str(content or "").strip()
        if not text:
            raise CollaborationProtocolError("Team message content is required")
        if len(text) > self.limits.max_team_message_chars:
            raise CollaborationProtocolError(
                f"Team message exceeds {self.limits.max_team_message_chars} characters"
            )

        known = set(self._team_unread)
        if sender_type == "agent" and sender_id not in known:
            raise CollaborationProtocolError(f"Unknown sending agent: {sender_id}")
        requested = tuple(dict.fromkeys(str(item) for item in recipient_agent_ids if str(item)))
        if any(item not in known for item in requested):
            raise CollaborationProtocolError("Team message targets an unknown Agent")
        if sender_type == "agent" and sender_id in requested:
            raise CollaborationProtocolError("An Agent cannot send a team message to itself")
        recipients = requested or tuple(
            agent_id for agent_id in self._team_unread if agent_id != sender_id
        )
        if not recipients:
            raise CollaborationProtocolError("Team message has no recipient")

        async with self._sequence_lock:
            if sender_type == "agent" and (
                self._collaboration_message_count >= self.limits.max_collaboration_messages
            ):
                raise CollaborationMessageBudgetExceeded(
                    "Agent collaboration message budget exhausted"
                )
            if reply_to_message_id:
                replied_to = self._team_messages.get(reply_to_message_id)
                if replied_to is None:
                    raise CollaborationProtocolError(
                        f"Unknown replied team message: {reply_to_message_id}"
                    )
                thread_id = thread_id or replied_to.thread_id or replied_to.message_id

            message = TeamMessage(
                run_id=self.request.run_id,
                context_scope_id=self.request.context_scope_id,
                sender_type=sender_type,
                sender_id=sender_id,
                content=text,
                recipient_agent_ids=recipients,
                channel="direct" if requested else "broadcast",
                thread_id=thread_id,
                reply_to_message_id=reply_to_message_id,
                expects_reply=expects_reply,
            )
            if expects_reply and not message.thread_id:
                message = replace(message, thread_id=message.message_id)
            creates_thread = bool(
                message.thread_id and message.thread_id not in self._team_open_threads
            )
            if creates_thread and len(self._team_open_threads) >= self.limits.max_open_threads:
                raise CollaborationProtocolError("Open collaboration thread budget exhausted")

            event = EventEnvelope(
                run_id=self.request.run_id,
                context_scope_id=self.request.context_scope_id,
                sequence=self.sequence + 1,
                type="collaboration.message_created",
                source=f"{sender_type}:{sender_id}",
                payload={
                    "sender_type": sender_type,
                    "sender_id": sender_id,
                    "recipient_agent_ids": list(recipients),
                    "channel": message.channel,
                    "thread_id": message.thread_id,
                    "reply_to_message_id": reply_to_message_id,
                    "content": text,
                    "expects_reply": expects_reply,
                    "status": message.status,
                },
            )
            try:
                persisted, persisted_event = await self.team_journal.append_message(
                    message, event
                )
            except CollaborationProtocolError:
                raise
            except Exception as exc:
                self._collaboration_failure_reason = "event_store_error"
                raise EventJournalError("Failed to persist team message") from exc
            self._team_messages[persisted.message_id] = persisted
            for recipient in recipients:
                self._team_unread[recipient].append(persisted.message_id)
            if creates_thread and persisted.thread_id:
                self._team_open_threads.add(persisted.thread_id)
            if sender_type == "agent":
                self._collaboration_message_count += 1
            self._watchdog.record_progress()
            await self._accept_committed_team_event(persisted_event)
        await self._publish_committed_event(persisted_event)
        return persisted

    async def _consume_team_inbox(self, agent_id: str) -> tuple[TeamMessage, ...]:
        if self.team_journal is None:
            return ()
        consumed: list[TeamMessage] = []
        while self._team_unread.get(agent_id):
            message_id = self._team_unread[agent_id][0]
            async with self._sequence_lock:
                event = EventEnvelope(
                    run_id=self.request.run_id,
                    context_scope_id=self.request.context_scope_id,
                    sequence=self.sequence + 1,
                    type="collaboration.message_consumed",
                    source="kernel",
                    target=agent_id,
                    payload={"message_id": message_id, "agent_id": agent_id},
                )
                try:
                    updated, persisted_event = await self.team_journal.mark_consumed(
                        message_id, agent_id, event
                    )
                except Exception as exc:
                    self._collaboration_failure_reason = "event_store_error"
                    raise EventJournalError("Failed to consume team message") from exc
                self._team_messages[message_id] = updated
                self._team_unread[agent_id].pop(0)
                consumed.append(updated)
                await self._accept_committed_team_event(persisted_event)
            await self._publish_committed_event(persisted_event)
        return tuple(consumed)

    def _collaboration_snapshot(self) -> CollaborationSnapshot:
        return CollaborationSnapshot(
            unread_by_agent={
                agent_id: tuple(
                    self._team_messages[message_id]
                    for message_id in message_ids
                    if message_id in self._team_messages
                )
                for agent_id, message_ids in self._team_unread.items()
            },
            open_thread_ids=tuple(sorted(self._team_open_threads)),
            agent_turn_counts=dict(self._agent_turn_counts),
            message_count=self._collaboration_message_count,
            message_budget_remaining=max(
                0,
                self.limits.max_collaboration_messages - self._collaboration_message_count,
            ),
            summary_agent_id=self._summary_agent_id,
            summary_scheduled=self._summary_scheduled,
            summary_completed=self._summary_completed,
        )

    async def _prepare_collaboration_complete(self) -> bool:
        async with self._sequence_lock:
            if any(self._team_unread.values()):
                return False
            self._accepting_team_messages = False
            return True

    async def _accept_committed_team_event(self, event: EventEnvelope) -> None:
        self.sequence = event.sequence
        self._live_events[event.sequence] = event
        self._last_event = event
        await self._notify_event_readers()

    async def _publish_committed_event(self, event: EventEnvelope) -> None:
        if self.event_sink is not None:
            try:
                await self.event_sink.emit(event)
            except Exception:
                pass

    async def _record_collaboration_rejection(self, source: str, reason: str) -> None:
        if not self._journal_created or self.state.is_terminal:
            return
        try:
            await self._emit(
                "collaboration.rejected",
                {"reason": reason[:500]},
                source=source,
            )
        except Exception:
            pass

    async def cancel(self, reason: str) -> RunResult:
        if self.state.is_terminal:
            return await self.result_future
        first_request = self.cancellation.cancel(reason or "user_cancelled")
        if first_request:
            self.state = RunState.CANCELLING
            try:
                await self._emit(
                    "control.cancel",
                    {"reason": self.cancellation.reason},
                    source="caller",
                    require_lease=False,
                )
            except EventSequenceConflictError:
                await self._finish(RunState.FAILED, "event_sequence_conflict")
                return await self.result_future
            except EventJournalError:
                await self._finish(RunState.FAILED, "event_store_error")
                return await self.result_future
            await asyncio.gather(
                *(actor.request_cancel(self.cancellation.reason or "user_cancelled") for actor in self._actors.values()),
                return_exceptions=True,
            )
            async with self._sequence_lock:
                self._cancel_work()
                if self._main_task and self._main_task is not asyncio.current_task():
                    self._main_task.cancel()
        try:
            return await asyncio.wait_for(
                asyncio.shield(self.result_future),
                timeout=self.limits.cancellation_grace_seconds,
            )
        except asyncio.TimeoutError:
            self.lease.revoke()
            await self._stop_actors(force=True)
            await self._finish(RunState.CANCELLED, self.cancellation.reason or "user_cancelled")
            return await self.result_future

    async def fail(self, reason_code: str) -> RunResult:
        if self.state.is_terminal:
            return await self.result_future
        self._forced_failure_reason = reason_code
        self.cancellation.cancel(reason_code)
        self._cancel_work()
        if self._main_task and self._main_task is not asyncio.current_task():
            self._main_task.cancel()
        return await self.result_future

    async def _run(self) -> None:
        try:
            self.started_at = utc_now()
            self._context = await self.context_store.load(self.request.context_scope_id)
            self._blackboard = VersionedBlackboard(self._context.blackboard)
            self._memories = dict(self._context.agent_memories)
            try:
                await self.run_journal.create_run(self.request, self.snapshot())
            except Exception as exc:
                raise EventJournalError("Failed to create runtime journal") from exc
            self._journal_created = True
            self._journal_ready.set()
            self.state = RunState.RUNNING
            if self._collaboration_enabled:
                known_agent_ids = {agent.id for agent in self.request.agents}
                if self.team_journal is None:
                    raise CollaborationProtocolError("Collaborative Run requires a TeamJournal")
                if self._summary_agent_id and self._summary_agent_id not in known_agent_ids:
                    raise CollaborationProtocolError("summary_agent_id is not a Run member")
            self._start_actors()
            self._watchdog_task = asyncio.create_task(
                self._watchdog.run(), name=f"runtime-watchdog:{self.request.run_id}"
            )
            await self._emit(
                "system.run_started",
                {
                    "run_id": self.request.run_id,
                    "context_scope_id": self.request.context_scope_id,
                },
            )
            while not self.state.is_terminal:
                self.cancellation.raise_if_cancelled()
                budget_reason = self._watchdog.check_decisions(self.decision_count)
                if budget_reason is not None:
                    await self._abort(budget_reason)
                    return
                try:
                    proposal = await self.request.policy.propose(
                        await self._policy_snapshot(), self._last_event
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    await self._abort("policy_error")
                    return
                self.decision_count += 1
                self.usage.add(proposal.usage)
                budget_reason = self._watchdog.check_tokens(self.usage.total_tokens)
                if budget_reason is not None:
                    await self._abort(budget_reason)
                    return
                self._last_event = await self._emit(
                    "scheduler.proposal",
                    {
                        "decision": self.decision_count,
                        "action": proposal.action,
                        "target_agent_ids": list(proposal.target_agent_ids),
                        "task": proposal.task,
                        "rationale": proposal.rationale,
                    },
                    source="policy",
                )
                if proposal.action == "complete":
                    budget_reason = self._current_budget_reason()
                    if budget_reason is not None:
                        await self._abort(budget_reason)
                        return
                    if self._collaboration_enabled and not await self._prepare_collaboration_complete():
                        continue
                    await self._stop_actors()
                    await self._commit_context()
                    output = self._summary_output or "\n\n".join(self._outputs)
                    await self._finish(RunState.COMPLETED, "completed", output)
                    return
                if proposal.action in {"assign", "parallel"}:
                    targets = proposal.target_agent_ids
                    if proposal.action == "assign":
                        targets = targets[:1]
                    await self._execute_targets(targets, proposal.task, proposal.metadata)
                    continue
                if proposal.action == "wait":
                    self.no_progress_count += 1
                    budget_reason = self._watchdog.check_no_progress(self.no_progress_count)
                    if budget_reason is not None:
                        await self._abort(budget_reason)
                        return
                    await asyncio.sleep(0)
                    continue
                await self._abort("policy_error")
                return
        except asyncio.CancelledError:
            await self._stop_actors(force=True)
            state = RunState.FAILED if self._forced_failure_reason else RunState.CANCELLED
            reason = self._forced_failure_reason or self.cancellation.reason or "user_cancelled"
            await self._finish(
                state,
                reason,
            )
        except AgentTurnBudgetExceeded:
            await self._abort("agent_turn_budget_exhausted")
        except CollaborationMessageBudgetExceeded:
            await self._abort("collaboration_message_budget_exhausted")
        except CollaborationProtocolError:
            await self._abort("collaboration_protocol_error")
        except ContextConflictError:
            await self._abort("context_conflict")
        except AdapterTimeoutError:
            await self._abort("adapter_timeout")
        except AdapterNotCancellableError:
            await self._abort("adapter_not_cancellable")
        except OutputTokenLimitExceeded:
            await self._abort("token_budget_exhausted")
        except EventSequenceConflictError:
            await self._abort("event_sequence_conflict")
        except EventJournalError:
            await self._abort("event_store_error")
        except Exception:
            await self._abort("internal_error")

    async def _policy_snapshot(self) -> PolicySnapshot:
        assert self._context is not None and self._blackboard is not None
        _, value = await self._blackboard.read()
        context = ContextSnapshot(
            scope_id=self._context.scope_id,
            version=self._context.version,
            messages=self._context.messages,
            blackboard=value,
            agent_memories=dict(self._memories),
        )
        collaboration = self._collaboration_snapshot() if self._collaboration_enabled else None
        return PolicySnapshot(
            run_id=self.request.run_id,
            context_scope_id=self.request.context_scope_id,
            input=self.request.input,
            agents=tuple(deepcopy(self.request.agents)),
            context=context,
            reports=tuple(self._reports),
            decision_count=self.decision_count,
            metadata=deepcopy(self.request.metadata),
            collaboration=collaboration,
        )

    async def _execute_targets(
        self, targets: tuple[str, ...], task: str, proposal_metadata: dict[str, Any] | None = None
    ) -> None:
        known = {agent.id: agent for agent in self.request.agents}
        allowed = {
            str(agent_id)
            for agent_id in self.request.metadata.get("allowed_agent_ids", known)
        }
        if (
            not targets
            or any(target not in known for target in targets)
            or any(target not in allowed for target in targets)
        ):
            await self._abort("policy_error")
            return
        proposal_metadata = dict(proposal_metadata or {})
        is_summary = bool(proposal_metadata.get("collaboration_summary"))
        if is_summary:
            if self._summary_scheduled or targets != (self._summary_agent_id,):
                await self._abort("collaboration_protocol_error")
                return
            self._summary_scheduled = True
        requests: list[AgentExecutionRequest] = []
        for target in targets:
            if self._collaboration_enabled:
                if self._agent_turn_counts[target] >= self.limits.max_agent_turns:
                    raise AgentTurnBudgetExceeded(target)
                self._agent_turn_counts[target] += 1
            inbox = ()
            if self._collaboration_enabled:
                inbox = (
                    tuple(sorted(self._team_messages.values(), key=lambda item: item.sequence))
                    if is_summary
                    else await self._consume_team_inbox(target)
                )
            metadata = {**deepcopy(self.request.metadata), **deepcopy(proposal_metadata)}
            if is_summary:
                metadata["open_collaboration_thread_ids"] = sorted(self._team_open_threads)
            requests.append(
                AgentExecutionRequest(
                    run_id=self.request.run_id,
                    context_scope_id=self.request.context_scope_id,
                    agent=known[target],
                    task=task or self.request.input,
                    input=self.request.input,
                    context=(await self._policy_snapshot()).context,
                    token_budget_remaining=max(
                        0, self.limits.max_total_tokens - self.usage.total_tokens
                    ),
                    inbox=inbox,
                    team_messenger=None if is_summary else (self if self._collaboration_enabled else None),
                    metadata=metadata,
                )
            )
        for target in targets:
            await self._emit(
                "control.assign",
                {"agent_id": target, "task": task or self.request.input},
                source="kernel",
                target=target,
                causation_id=self._last_event.event_id if self._last_event else None,
            )
        tasks = {
            asyncio.create_task(
                self._actors[request.agent.id].assign(request),
                name=f"runtime-agent:{self.request.run_id}:{request.agent.id}",
            )
            for request in requests
        }
        self._managed_tasks.update(tasks)
        try:
            results = await asyncio.gather(*tasks)
        finally:
            self._managed_tasks.difference_update(tasks)
        for result in results:
            await self._accept_agent_result(result, is_summary=is_summary)
        if self._collaboration_failure_reason:
            await self._abort(self._collaboration_failure_reason)

    async def _accept_agent_result(
        self, result: AgentExecutionResult, *, is_summary: bool = False
    ) -> None:
        self.lease.require_valid()
        self.usage.add(result.usage)
        budget_reason = self._watchdog.check_tokens(self.usage.total_tokens)
        if budget_reason is not None:
            await self._abort(budget_reason)
            return
        self._reports.append(result.report)
        if result.output:
            self._outputs.append(result.output)
            if is_summary:
                self._summary_output = result.output
        if is_summary:
            self._summary_completed = True
        if result.memory is not None:
            self._memories[result.agent_id] = result.memory
        if result.blackboard_update:
            assert self._blackboard is not None
            version, _ = await self._blackboard.read()
            await self._blackboard.update(version, result.blackboard_update)
        if result.progress:
            self.no_progress_count = 0
            self._watchdog.record_progress()
        else:
            self.no_progress_count += 1
            budget_reason = self._watchdog.check_no_progress(self.no_progress_count)
            if budget_reason is not None:
                await self._abort(budget_reason)
                return
        self._last_event = await self._emit(
            "agent.report",
            {
                "agent_id": result.agent_id,
                "state": result.report.state.value,
                "will": result.report.will.value,
                "work_product": result.output,
                "report": {
                    "agent_id": result.report.agent_id,
                    "state": result.report.state.value,
                    "will": result.report.will.value,
                    "target_task": result.report.target_task,
                    "blockers": list(result.report.blockers),
                    "confidence": result.report.confidence,
                    "rationale": result.report.rationale,
                },
            },
            source=f"agent:{result.agent_id}",
        )

    async def _commit_context(self) -> None:
        self.lease.require_valid()
        assert self._context is not None and self._blackboard is not None
        _, blackboard = await self._blackboard.read()
        self._context = await self.context_store.commit(
            self.request.context_scope_id,
            ContextDelta(
                expected_version=self._context.version,
                blackboard=blackboard,
                messages=(
                    *self._context.messages,
                    {"role": "user", "content": self.request.input},
                    *(
                        ({"role": "assistant", "content": output} for output in self._outputs)
                    ),
                ),
                agent_memories=dict(self._memories),
            ),
        )

    async def _executor_emit(
        self,
        event_type: str,
        payload: dict,
        source: str = "agent",
        target: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> None:
        try:
            await self._emit(
                event_type,
                payload,
                source=source,
                target=target,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
        except Exception:
            if not self.lease.valid:
                await self._emit_rejected(event_type, source)
            raise

    async def _emit_rejected(self, event_type: str, source: str) -> None:
        async with self._sequence_lock:
            event = EventEnvelope(
                run_id=self.request.run_id,
                context_scope_id=self.request.context_scope_id,
                sequence=self.sequence + 1,
                type="system.late_event_rejected",
                payload={"rejected_type": event_type},
                source=source,
            )
            try:
                await self.run_journal.append_event(event)
            except Exception:
                return
            self.sequence = event.sequence
            self._live_events[event.sequence] = event
            await self._notify_event_readers()
        if self.event_sink is not None:
            try:
                await self.event_sink.emit(event)
            except Exception:
                pass

    def _current_budget_reason(self) -> str | None:
        return (
            self._watchdog.reason()
            or self._watchdog.check_tokens(self.usage.total_tokens)
            or self._watchdog.check_no_progress(self.no_progress_count)
        )

    async def _fail_from_watchdog(self, reason_code: str) -> None:
        await self._abort(reason_code)

    async def _abort(self, reason_code: str) -> None:
        if self.state.is_terminal:
            return
        self._forced_failure_reason = reason_code
        self.cancellation.cancel(reason_code)
        self._cancel_work()
        if self._main_task and self._main_task is not asyncio.current_task():
            self._main_task.cancel()
            return
        await self._stop_actors(force=True)
        await self._finish(RunState.FAILED, reason_code)

    def _start_actors(self) -> None:
        for agent in self.request.agents:
            actor = AgentActor(
                run_id=self.request.run_id,
                agent_config=agent,
                executor=self.agent_executor,
                emit=self._executor_emit,
                cancellation=self.cancellation,
                lease=self.lease,
            )
            self._actors[agent.id] = actor
            actor.start()

    def _cancel_work(self) -> None:
        for task in list(self._managed_tasks):
            task.cancel()
        for actor in self._actors.values():
            if actor.task is not None:
                actor.task.cancel()

    async def _stop_actors(self, *, force: bool = False) -> None:
        if not self._actors:
            return
        await asyncio.gather(
            *(actor.stop(force=force) for actor in self._actors.values()),
            return_exceptions=True,
        )

    async def _emit(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: str = "kernel",
        target: str | None = None,
        correlation_id: str | None = None,
        causation_id: str | None = None,
        require_lease: bool = True,
    ) -> EventEnvelope:
        if require_lease:
            self.lease.require_valid()
        async with self._sequence_lock:
            event = EventEnvelope(
                run_id=self.request.run_id,
                context_scope_id=self.request.context_scope_id,
                sequence=self.sequence + 1,
                type=event_type,
                payload=deepcopy(payload),
                source=source,
                target=target,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )
            await self._append_event(event)
            self.sequence = event.sequence
            self._live_events[event.sequence] = event
            await self._notify_event_readers()
        if self.event_sink is not None:
            try:
                await self.event_sink.emit(event)
            except Exception:
                pass
        return event

    async def _finish(self, state: RunState, reason_code: str, output: str = "") -> bool:
        async with self._finish_lock:
            if self.state.is_terminal:
                return False
            self._accepting_team_messages = False
            if self._collaboration_enabled and self.team_journal is not None:
                if state is not RunState.COMPLETED:
                    try:
                        await self.team_journal.interrupt_run(self.request.run_id)
                    except Exception:
                        pass
            self.finished_at = utc_now()
            result = RunResult(
                run_id=self.request.run_id,
                context_scope_id=self.request.context_scope_id,
                state=state,
                reason_code=reason_code,
                started_at=self.started_at or self.finished_at,
                finished_at=self.finished_at,
                usage=Usage(
                    prompt_tokens=self.usage.prompt_tokens,
                    completion_tokens=self.usage.completion_tokens,
                    estimated=self.usage.estimated,
                ),
                context_version=self._context.version if self._context is not None else 0,
                output=output,
            )
            terminal_type = {
                RunState.COMPLETED: "system.run_completed",
                RunState.FAILED: "system.run_failed",
                RunState.CANCELLED: "system.run_cancelled",
            }[state]
            async with self._sequence_lock:
                terminal_event = EventEnvelope(
                    run_id=self.request.run_id,
                    context_scope_id=self.request.context_scope_id,
                    sequence=self.sequence + 1,
                    type=terminal_type,
                    payload={
                        "state": state.value,
                        "reason_code": reason_code,
                        "usage": result.usage.to_dict(),
                        "output": output,
                    },
                )
                try:
                    persisted = await self.run_journal.try_finish(result, terminal_event)
                except EventSequenceConflictError:
                    await self._finish_locally_after_journal_failure(
                        result, reason_code="event_sequence_conflict"
                    )
                    return True
                except Exception:
                    await self._finish_locally_after_journal_failure(
                        result, reason_code="event_store_error"
                    )
                    return True
                if not persisted:
                    return False
                self.sequence = terminal_event.sequence
                self._live_events[terminal_event.sequence] = terminal_event
                await self._notify_event_readers()
            self.state = state
            self.reason_code = reason_code
            self._watchdog.stop()
            if self._watchdog_task and self._watchdog_task is not asyncio.current_task():
                self._watchdog_task.cancel()
            if self.event_sink is not None:
                try:
                    await self.event_sink.emit(terminal_event)
                except Exception:
                    pass
            self.lease.revoke()
            if not self.result_future.done():
                self.result_future.set_result(result)
            if self._on_terminal is not None:
                self._on_terminal(self.request.run_id, None)
            return True

    async def _append_event(self, event: EventEnvelope) -> None:
        try:
            await self.run_journal.append_event(event)
        except EventSequenceConflictError:
            raise
        except Exception as exc:
            raise EventJournalError("Failed to persist runtime event") from exc

    async def _notify_event_readers(self) -> None:
        async with self._event_condition:
            self._event_condition.notify_all()

    async def _finish_locally_after_journal_failure(
        self, attempted: RunResult, *, reason_code: str
    ) -> None:
        self.finished_at = utc_now()
        self._journal_failed = True
        self.state = RunState.FAILED
        self.reason_code = reason_code
        self._watchdog.stop()
        if self._watchdog_task and self._watchdog_task is not asyncio.current_task():
            self._watchdog_task.cancel()
        self.lease.revoke()
        failed = RunResult(
            run_id=attempted.run_id,
            context_scope_id=attempted.context_scope_id,
            state=RunState.FAILED,
            reason_code=reason_code,
            started_at=attempted.started_at,
            finished_at=self.finished_at,
            usage=attempted.usage,
            context_version=attempted.context_version,
        )
        if not self.result_future.done():
            self.result_future.set_result(failed)
        self._journal_ready.set()
        await self._notify_event_readers()
        if self._on_terminal is not None:
            self._on_terminal(self.request.run_id, None)
