"""RuntimeEngine, RunHandle, and the single-owner RunKernel."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from copy import deepcopy
from typing import Any

from model_provider.core.streaming import OutputTokenLimitExceeded

from ..context.scope_store import InMemoryContextStore, VersionedBlackboard
from ..core.ports import AgentExecutor, ContextConflictError, ContextStore, RunEventSink, RunJournal
from ..core.run_types import (
    AgentExecutionRequest,
    AgentExecutionResult,
    AgentMemory,
    ContextDelta,
    ContextSnapshot,
    EventEnvelope,
    PolicySnapshot,
    RunRequest,
    RunResult,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    Usage,
    utc_now,
)
from .cancellation import CancellationScope, RunLease
from .adapter_isolation import AdapterNotCancellableError, AdapterTimeoutError
from .agent_actor import AgentActor
from .run_journal import EventJournalError, EventSequenceConflictError, InMemoryRunJournal
from .run_watchdog import RunWatchdog


class RuntimeEngine:
    """Reusable dependency container that starts isolated one-shot runs."""

    def __init__(
        self,
        *,
        agent_executor: AgentExecutor,
        context_store: ContextStore | None = None,
        run_journal: RunJournal | None = None,
        event_sink: RunEventSink | None = None,
        limits: RuntimeLimits | None = None,
    ) -> None:
        self.agent_executor = agent_executor
        self.context_store = context_store or InMemoryContextStore()
        self.run_journal = run_journal or InMemoryRunJournal()
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
        event_sink: RunEventSink | None,
        limits: RuntimeLimits,
        on_terminal=None,
    ) -> None:
        self.request = request
        self.agent_executor = agent_executor
        self.context_store = context_store
        self.run_journal = run_journal
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
                    await self._stop_actors()
                    await self._commit_context()
                    await self._finish(RunState.COMPLETED, "completed", "\n\n".join(self._outputs))
                    return
                if proposal.action in {"assign", "parallel"}:
                    targets = proposal.target_agent_ids
                    if proposal.action == "assign":
                        targets = targets[:1]
                    await self._execute_targets(targets, proposal.task)
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
        return PolicySnapshot(
            run_id=self.request.run_id,
            context_scope_id=self.request.context_scope_id,
            input=self.request.input,
            agents=tuple(deepcopy(self.request.agents)),
            context=context,
            reports=tuple(self._reports),
            decision_count=self.decision_count,
            metadata=deepcopy(self.request.metadata),
        )

    async def _execute_targets(self, targets: tuple[str, ...], task: str) -> None:
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
        requests = [
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
                metadata=deepcopy(self.request.metadata),
            )
            for target in targets
        ]
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
            await self._accept_agent_result(result)

    async def _accept_agent_result(self, result: AgentExecutionResult) -> None:
        self.lease.require_valid()
        self.usage.add(result.usage)
        budget_reason = self._watchdog.check_tokens(self.usage.total_tokens)
        if budget_reason is not None:
            await self._abort(budget_reason)
            return
        self._reports.append(result.report)
        if result.output:
            self._outputs.append(result.output)
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
