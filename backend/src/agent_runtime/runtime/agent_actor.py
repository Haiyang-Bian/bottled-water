"""Run-scoped AgentActor driven exclusively through a Mailbox."""

from __future__ import annotations

import asyncio

from ..core.ports import AgentExecutor, EmitEvent
from ..core.protocol import CONTROL_ASSIGN, CONTROL_CANCEL, CONTROL_SHUTDOWN
from ..core.run_types import AgentExecutionRequest, AgentExecutionResult
from ..core.types import AgentConfig, Event
from .cancellation import CancellationScope, RunLease
from .mailbox import Mailbox


class AgentActor:
    """One Run-owned Agent task with a private control Mailbox."""

    def __init__(
        self,
        *,
        run_id: str,
        agent_config: AgentConfig,
        executor: AgentExecutor,
        emit: EmitEvent,
        cancellation: CancellationScope,
        lease: RunLease,
    ) -> None:
        self.run_id = run_id
        self.config = agent_config
        self.executor = executor
        self.emit = emit
        self.cancellation = cancellation
        self.lease = lease
        self.mailbox = Mailbox(agent_config.id)
        self._task: asyncio.Task[None] | None = None
        self._pending: set[asyncio.Future[AgentExecutionResult]] = set()

    @property
    def task(self) -> asyncio.Task[None] | None:
        return self._task

    def start(self) -> asyncio.Task[None]:
        if self._task is not None:
            raise RuntimeError(f"AgentActor already started: {self.config.id}")
        self._task = asyncio.create_task(
            self.run(), name=f"runtime-actor:{self.run_id}:{self.config.id}"
        )
        return self._task

    async def assign(self, request: AgentExecutionRequest) -> AgentExecutionResult:
        if request.run_id != self.run_id or request.agent.id != self.config.id:
            raise ValueError("AgentActor assignment target does not match its Run and Agent")
        if self._task is None or self._task.done():
            raise RuntimeError(f"AgentActor is not running: {self.config.id}")
        future = asyncio.get_running_loop().create_future()
        self._pending.add(future)
        await self.mailbox.send(
            Event(
                type=CONTROL_ASSIGN,
                payload={"request": request, "result_future": future},
                source="kernel",
                target=self.config.id,
                channel="internal",
            )
        )
        try:
            return await future
        finally:
            self._pending.discard(future)

    async def request_cancel(self, reason: str) -> None:
        if self._task is None or self._task.done():
            return
        await self.mailbox.send(
            Event(
                type=CONTROL_CANCEL,
                payload={"reason": reason},
                source="kernel",
                target=self.config.id,
                channel="internal",
            )
        )

    async def stop(self, *, force: bool = False) -> None:
        task = self._task
        if task is None or task.done():
            return
        if force:
            task.cancel()
        else:
            await self.mailbox.send(
                Event(
                    type=CONTROL_SHUTDOWN,
                    payload={"reason": "run_finished"},
                    source="kernel",
                    target=self.config.id,
                    channel="internal",
                )
            )
        await asyncio.gather(task, return_exceptions=True)

    async def run(self) -> None:
        current: asyncio.Future[AgentExecutionResult] | None = None
        try:
            while True:
                event = await self.mailbox.recv()
                if event.type in {CONTROL_CANCEL, CONTROL_SHUTDOWN}:
                    return
                if event.type != CONTROL_ASSIGN:
                    continue
                request = event.payload.get("request")
                candidate = event.payload.get("result_future")
                if not isinstance(request, AgentExecutionRequest) or not isinstance(
                    candidate, asyncio.Future
                ):
                    continue
                current = candidate
                try:
                    result = await self.executor.execute(
                        request,
                        emit=self.emit,
                        cancellation=self.cancellation,
                        lease=self.lease,
                    )
                    self.lease.require_valid()
                    if not current.done():
                        current.set_result(result)
                except asyncio.CancelledError:
                    if not current.done():
                        current.cancel()
                    raise
                except Exception as exc:
                    if not current.done():
                        current.set_exception(exc)
                finally:
                    current = None
        finally:
            if current is not None and not current.done():
                current.cancel()
            for future in self._pending:
                if not future.done():
                    future.cancel()
            self.mailbox.close()
