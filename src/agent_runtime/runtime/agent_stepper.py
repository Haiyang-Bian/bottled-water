"""Step-oriented wrapper around AgentLoop.

The existing AgentLoop remains compatible. AgentStepper adds control-event
checkpoints around each assignment so Actor runtime sessions can pause,
resume, reassign, cancel, and complete without changing old callers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from ..context.agent_ctx import AgentContext
from ..core.interfaces import AgentContextProvider, ToolExecutor
from ..core.protocol import (
    CONTROL_ASSIGN,
    CONTROL_CANCEL,
    CONTROL_COMPLETE,
    CONTROL_PAUSE,
    CONTROL_RESUME,
    CONTROL_SHUTDOWN,
)
from ..core.types import AgentReport, AgentState, AgentWill, Event
from agent_subsystems.execution.agent_loop import AgentLoop
from .mailbox import Mailbox


@dataclass
class StepResult:
    task: str
    output: dict[str, Any]
    report: AgentReport
    interrupted: bool = False


class AgentControlInterrupt(Exception):
    """Raised when a control event interrupts the current assignment."""


class AgentStepper:
    """Runs one Agent assignment with control checkpoints."""

    def __init__(self, agent_loop: AgentLoop, mailbox: Mailbox) -> None:
        self.agent_loop = agent_loop
        self.mailbox = mailbox
        self.state = AgentState.IDLE
        self.current_task = ""
        self.cancel_requested = False
        self.complete_requested = False

    async def run_assignment(
        self,
        task: str,
        blackboard_view: dict[str, Any],
        *,
        tool_executor: ToolExecutor | None = None,
        agent_ctx: AgentContext | None = None,
        emit_event=None,
        context_provider: AgentContextProvider | None = None,
        context_metadata: dict[str, Any] | None = None,
    ) -> StepResult:
        self.current_task = task
        await self._apply_pending_controls()
        await self._wait_if_paused()
        if self.cancel_requested or self.complete_requested:
            return StepResult(
                task=self.current_task,
                output={},
                report=self.terminal_report(),
                interrupted=True,
            )

        self.state = AgentState.RUNNING
        try:
            output = await self.agent_loop.run(
                self.current_task,
                blackboard_view,
                tool_executor=tool_executor,
                agent_ctx=agent_ctx,
                emit_event=self._checkpoint_emitter(emit_event),
                checkpoint=self._checkpoint,
                context_provider=context_provider,
                context_metadata=context_metadata,
            )
        except AgentControlInterrupt:
            return StepResult(
                task=self.current_task,
                output={},
                report=self.terminal_report(),
                interrupted=True,
            )
        await asyncio.sleep(0)
        await self._apply_pending_controls()
        report = output.get("status_report")
        if not isinstance(report, AgentReport):
            report = self.terminal_report()
        self.state = report.state
        return StepResult(task=self.current_task, output=output, report=report)

    def _checkpoint_emitter(self, emit_event):
        async def _emit(event: Event) -> None:
            if emit_event:
                await emit_event(event)
            await asyncio.sleep(0)
            await self._apply_pending_controls()
            await self._wait_if_paused()
            if self.cancel_requested or self.complete_requested:
                raise AgentControlInterrupt()

        return _emit

    async def _checkpoint(self, _stage: str, _payload: dict[str, Any] | None = None) -> None:
        await asyncio.sleep(0)
        await self._apply_pending_controls()
        await self._wait_if_paused()
        if self.cancel_requested or self.complete_requested:
            raise AgentControlInterrupt()

    async def _apply_pending_controls(self) -> None:
        for event in self.mailbox.drain():
            await self.apply_control(event)

    async def apply_control(self, event: Event) -> None:
        if event.type == CONTROL_ASSIGN:
            task = event.payload.get("task") or event.payload.get("task_description")
            if task:
                self.current_task = str(task)
            if self.state == AgentState.PAUSED:
                self.state = AgentState.READY
        elif event.type == CONTROL_PAUSE:
            self.state = AgentState.PAUSED
        elif event.type == CONTROL_RESUME:
            if self.state == AgentState.PAUSED:
                self.state = AgentState.READY
        elif event.type in {CONTROL_CANCEL, CONTROL_SHUTDOWN}:
            self.cancel_requested = True
            self.state = AgentState.FAILED
        elif event.type == CONTROL_COMPLETE:
            self.complete_requested = True
            self.state = AgentState.COMPLETED

    async def _wait_if_paused(self) -> None:
        while self.state == AgentState.PAUSED and not self.cancel_requested:
            event = await self.mailbox.recv()
            await self.apply_control(event)

    def terminal_report(self) -> AgentReport:
        if self.cancel_requested:
            return AgentReport(
                agent_id=self.agent_loop.agent.id,
                state=AgentState.FAILED,
                will=AgentWill.BLOCKED,
                rationale="Agent execution cancelled by control event",
                confidence=1.0,
            )
        return AgentReport(
            agent_id=self.agent_loop.agent.id,
            state=AgentState.COMPLETED,
            will=AgentWill.COMPLETE,
            rationale="Agent completed by control event",
            confidence=1.0,
        )
