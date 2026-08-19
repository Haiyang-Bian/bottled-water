"""AgentLoop adapter for the Runtime Kernel AgentExecutor port."""

from __future__ import annotations

from typing import Any

from ..core.run_types import AgentExecutionResult, AgentMemory, Usage
from ..core.types import Event
from .agent_loop import AgentLoop
from .team_tools import TeamToolExecutor


class AgentLoopExecutor:
    def __init__(
        self,
        *,
        model_provider,
        tool_executor=None,
        context_provider=None,
        use_streaming: bool = True,
    ) -> None:
        self.model_provider = model_provider
        self.tool_executor = tool_executor
        self.context_provider = context_provider
        self.use_streaming = use_streaming

    async def execute(self, request, *, emit, cancellation, lease) -> AgentExecutionResult:
        loop = AgentLoop(
            request.agent,
            self.model_provider,
            use_streaming=self.use_streaming,
            max_output_tokens=request.token_budget_remaining,
        )

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
                self.tool_executor,
                request.team_messenger,
                request.agent.id,
            )

        result = await loop.run(
            task,
            request.context.blackboard,
            tool_executor=tool_executor,
            emit_event=emit_legacy,
            checkpoint=checkpoint,
            context_provider=self.context_provider,
            context_metadata=metadata,
        )
        report = result["status_report"]
        output = str(result.get("work_product") or "")
        return AgentExecutionResult(
            agent_id=request.agent.id,
            report=report,
            output=output,
            usage=Usage(
                prompt_tokens=int((result.get("usage") or {}).get("prompt_tokens") or 0),
                completion_tokens=int((result.get("usage") or {}).get("completion_tokens") or 0),
                estimated=bool(result.get("usage_estimated")),
            ),
            memory=AgentMemory(
                agent_id=request.agent.id,
                summary=output[:2000],
                completed_tasks=(request.task,) if output else (),
                blockers=tuple(report.blockers),
            ),
            progress=bool(output.strip()) or not report.blockers,
        )
