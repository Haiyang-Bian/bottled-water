"""AgentLoop adapter for the Runtime Kernel AgentExecutor port."""

from __future__ import annotations

from typing import Any
from agent_contracts.harness import ExecutionLimits, ExecutionStopped
from agent_runtime.core.types import AgentReport, AgentState, AgentWill
from agent_contracts.errors import ModelInvocationError, OutputTokenLimitExceeded

from agent_runtime.core.run_types import AgentExecutionResult, AgentMemory, Usage
from agent_runtime.core.types import Event
from .agent_loop import AgentLoop
from agent_subsystems.tools.history import HistoryToolExecutor, JournalResultReader
from .extensions import ExecutionExtension
from agent_runtime.runtime.team_tools import TeamToolExecutor


class AgentLoopExecutor:
    def __init__(
        self,
        *,
        model_provider,
        tool_executor=None,
        context_provider=None,
        use_streaming: bool = True,
        extension_factory=ExecutionExtension,
        execution_limits=None,
        context_budget=None,
        run_journal=None,
        memory_context=None,
        resource_context=None,
    ) -> None:
        self.context_budget, self.run_journal = context_budget, run_journal
        self.memory_context = memory_context
        self.resource_context = resource_context
        self.execution_limits = execution_limits or ExecutionLimits()
        self.model_provider = model_provider
        self.tool_executor = tool_executor
        self.context_provider = context_provider
        self.use_streaming = use_streaming
        self.extension_factory = extension_factory

    async def execute(self, request, *, emit, cancellation, lease) -> AgentExecutionResult:
        loop = AgentLoop(
            request.agent,
            self.model_provider,
            use_streaming=self.use_streaming,
            max_output_tokens=request.token_budget_remaining,
            extension_factory=self.extension_factory,
            execution_limits=self.execution_limits,
            observer=request.observer,
            deadline=request.deadline,
            context_budget=self.context_budget,
            run_id=request.run_id,
        )
        loop.context_snapshot = request.context
        loop.memory_context = self.memory_context
        loop.resource_context = self.resource_context

        async def emit_legacy(event: Event) -> None:
            cancellation.raise_if_cancelled()
            lease.require_valid()
            await emit(
                event.type,
                dict(event.payload or {}),
                event.source,
                event.target,
                event.correlation_id,
                None,
            )

        async def checkpoint(stage: str, payload: dict[str, Any]) -> None:
            cancellation.raise_if_cancelled()
            lease.require_valid()

        tool_executor = self.tool_executor
        bind_execution = getattr(tool_executor, "bind_execution", None)
        if callable(bind_execution):
            tool_executor = bind_execution(request, cancellation, lease)
        if self.run_journal is not None:
            tool_executor = HistoryToolExecutor(
                tool_executor, JournalResultReader(self.run_journal, request.context_scope_id)
            )
        task = request.task
        metadata = dict(request.metadata)
        if request.inbox:
            inbox_text = "\n".join(
                f"- [{message.message_id}] {message.sender_type}:{message.sender_id}: {message.content}"
                for message in request.inbox
            )
            task = f"{task}\n\n团队收件箱（仅包含主动发送给你的消息）：\n{inbox_text}"
            visible = str(metadata.get("visible_content") or request.input)
            metadata["visible_content"] = f"{visible}\n\n团队收件箱：\n{inbox_text}"
        if request.team_messenger is not None:
            tool_executor = TeamToolExecutor(
                tool_executor,
                request.team_messenger,
                request.agent.id,
            )

        try:
            result = await loop.run(
                task,
                request.context.blackboard,
                tool_executor=tool_executor,
                emit_event=emit_legacy,
                checkpoint=checkpoint,
                context_provider=self.context_provider,
                context_metadata=metadata,
            )
        except ExecutionStopped as exc:
            if exc.reason_code == "model_timeout":
                loop.usage_estimated = True
            return AgentExecutionResult(
                agent_id=request.agent.id,
                report=AgentReport(
                    agent_id=request.agent.id,
                    state=AgentState.FAILED,
                    will=AgentWill.BLOCKED,
                    blockers=[exc.reason_code],
                ),
                reason_code=exc.reason_code,
                execution_id=request.execution_id,
                usage=Usage(**loop.usage_snapshot()),
                counters=dict(loop.counters),
            )
        except (ModelInvocationError, OutputTokenLimitExceeded) as exc:
            exc.usage_accounted = request.observer is not None
            exc.usage = {
                **loop.usage_snapshot(),
                "estimated": loop.usage_estimated
                or isinstance(exc, ModelInvocationError)
                or not any(loop.usage.values()),
            }
            if request.observer:
                await request.observer.usage_reported("interrupted", exc.usage, dict(loop.counters))
            raise
        report = result["status_report"]
        output = str(result.get("work_product") or "")
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=report,
            output=output,
            usage=Usage(**loop.usage_snapshot()),
            memory=AgentMemory(
                agent_id=request.agent.id,
                summary=output[:2000],
                completed_tasks=(request.task,) if report.state.value == "completed" else (),
                blockers=tuple(report.blockers),
            ),
            progress=None,
            execution_id=request.execution_id,
            counters=dict(loop.counters),
        )
