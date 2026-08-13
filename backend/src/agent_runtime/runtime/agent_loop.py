"""
Agent 执行循环

负责单个 Agent 的单轮执行：
- 构建上下文（私有栈 + Blackboard 视图）
- 调用 LLM（支持工具调用）
- 处理工具调用（多轮）
- 状态自报告
"""

import json
import asyncio
import inspect
import re
import uuid
from typing import Dict, Any, Optional, List

from model_provider.core.interfaces import BaseModelProvider, ChatMessage, ChatResponse
from model_provider.core.streaming import collect_chat_stream

from common.logger import get_logger
from ..core.types import AgentConfig, AgentReport, AgentState, AgentWill, ToolCall, ToolResult
from ..core.interfaces import (
    AgentContextBuildRequest,
    AgentContextBuildResult,
    AgentContextProvider,
    ToolExecutor,
)
from ..context.agent_ctx import AgentContext
from .status_report import parse_agent_status_report

logger = get_logger(__name__)


class _StatusReportStreamFilter:
    """Hide status_report fenced blocks while preserving the raw final response."""

    _fence = "```status_report"
    _names = ("status_report", "status")
    _fence_re = re.compile(r"^```\s*(?:status_report|status)\b", re.IGNORECASE)

    def __init__(self) -> None:
        self._raw = ""
        self._visible = ""

    def push(self, delta: str) -> str:
        if not delta:
            return ""
        self._raw += delta
        visible = self._strip_status_report(self._raw)
        if not visible:
            self._visible = ""
            return ""
        if visible.startswith(self._visible):
            new_text = visible[len(self._visible) :]
        else:
            new_text = ""
        self._visible = visible
        return new_text

    @classmethod
    def _strip_status_report(cls, text: str) -> str:
        lines = text.splitlines()
        visible: list[str] = []
        removed = False
        index = 0
        while index < len(lines):
            line = lines[index]
            trimmed = line.strip()
            lowered = trimmed.lower()

            if index == len(lines) - 1 and cls._can_become_status_fence(lowered):
                removed = True
                index += 1
                continue

            if lowered.startswith("```"):
                opening_could_be_internal = bool(
                    cls._fence_re.match(trimmed) or cls._can_become_status_fence(lowered)
                )
                body: list[str] = []
                cursor = index + 1
                while cursor < len(lines) and not lines[cursor].strip().startswith("```"):
                    body.append(lines[cursor])
                    cursor += 1

                closed = cursor < len(lines)
                if not closed:
                    removed = True
                    break

                if opening_could_be_internal or cls._body_looks_like_status_report(body):
                    removed = True
                    index = cursor + 1
                    continue

                visible.extend([line, *body, lines[cursor]])
                index = cursor + 1
                continue

            visible.append(line)
            index += 1
        cleaned = "\n".join(visible).strip()
        return cleaned if cleaned or not removed else ""

    @classmethod
    def _can_become_status_fence(cls, value: str) -> bool:
        if not value:
            return False
        normalized = value.strip().lower()
        if cls._fence.startswith(normalized):
            return True
        if cls._fence_re.match(normalized):
            return True
        partial = re.match(r"^```\s*([a-z_]*)$", normalized, flags=re.IGNORECASE)
        return bool(
            partial
            and any(name.startswith(partial.group(1).lower()) for name in cls._names)
        )

    @classmethod
    def _body_looks_like_status_report(cls, body_lines: list[str]) -> bool:
        first_meaningful = next((line.strip().lower() for line in body_lines if line.strip()), "")
        if not first_meaningful:
            return False
        if first_meaningful in cls._names:
            return True
        body = "\n".join(body_lines).lower()
        if not body.strip().startswith("{"):
            return False
        has_state = bool(re.search(r'"state"\s*:', body))
        has_status_fields = bool(
            re.search(r'"(?:will|rationale|blockers|priority|confidence)"\s*:', body)
        )
        return has_state and has_status_fields


class AgentLoop:
    """Agent 执行循环

    支持两种模式：
    1. 传统模式：run() 一次性执行完所有步骤
    2. 步进模式：step() 每次执行一步，支持外部干预
    """

    MAX_TOOL_ROUNDS = 10  # 最大工具调用轮数，防止无限循环

    def __init__(
        self,
        agent_config: AgentConfig,
        model_provider: BaseModelProvider,
        use_streaming: bool = False,
        max_output_tokens: int | None = None,
    ):
        self.agent = agent_config
        self.model = model_provider
        self.use_streaming = use_streaming
        self.max_output_tokens = max_output_tokens
        self.usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0}
        self.usage_estimated = False
        self._state = AgentState.IDLE
        self._step_data: Dict[str, Any] = {}

    @property
    def state(self) -> AgentState:
        """当前状态（只读）"""
        return self._state

    def _build_initial_thinking(self, task: str) -> str:
        text = (task or "").strip()
        compact = re.sub(r"\s+", "", text.lower())
        if not compact:
            return "我先确认这轮输入的目标，再决定是直接回答，还是结合上下文或工具继续处理。"

        greetings = {"你好", "您好", "hi", "hello", "嗨", "哈喽", "在吗", "你好吗"}
        if compact in greetings:
            return "这是一个简单的问候场景，用户暂时没有提出具体任务；我先自然回应，并简要说明我当前能提供的帮助范围。"

        if any(keyword in compact for keyword in ("pdf", "word", "docx", "ppt", "xlsx", "excel", "html", "网页", "文档", "报告", "方案")):
            return "用户希望生成文档或网页类产物；我会先判断目标格式和交付形式，再决定是否调用对应工具，并在回复里说明生成结果和后续可操作项。"

        if any(keyword in compact for keyword in ("总结", "概括", "提取", "摘要", "文件", "附件")):
            return "这轮更像是文件理解或内容总结任务；我会先判断有没有附件或现成上下文，再提炼关键信息，最后输出简洁结论。"

        if any(keyword in compact for keyword in ("代码", "运行", "脚本", "python", "javascript", "调试", "报错", "接口", "测试")):
            return "这是代码或执行类请求；我会先判断是否需要调用沙箱、测试或其他工具，再根据执行结果组织最终回复。"

        if any(keyword in compact for keyword in ("介绍", "解释", "分析", "怎么", "为什么", "会什么", "能做什么", "什么")):
            return "用户需要解释、介绍或分析型回答；我会先识别问题焦点和期望深度，再组织成清晰、可直接阅读的自然语言回复。"

        return "我正在分析这轮需求的目标、上下文和是否需要工具参与，然后给出最合适的回复路径。"

    def _set_state(self, new_state: AgentState):
        """设置状态并记录日志"""
        if self._state != new_state:
            logger.debug(
                "Agent 状态变更",
                agent_id=self.agent.id,
                old=self._state.value,
                new=new_state.value,
            )
            self._state = new_state

    async def run(
        self,
        task: str,
        blackboard_view: dict,
        tool_executor: Optional[ToolExecutor] = None,
        agent_ctx: Optional[AgentContext] = None,
        emit_event=None,
        checkpoint=None,
        context_provider: Optional[AgentContextProvider] = None,
        context_metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        执行 Agent 单轮任务。

        流程：
        1. 注入 AgentContext 历史帧到消息列表
        2. 构建用户提示词
        3. 调用 LLM（带工具列表）
        4. 如果 LLM 返回 tool_calls，执行工具并回传结果
        5. 重复步骤 3-4 直到 LLM 不再调用工具或达到上限
        6. 解析最终回复，提取成果和状态报告
        7. 将本轮对话归档回 AgentContext

        Args:
            emit_event: 可选的事件发射回调，签名 async fn(event: Event) -> None

        Returns:
            {"work_product": str, "status_report": AgentReport}
        """
        from ..core.types import Event

        agent_source = f"agent:{self.agent.id}"

        async def _emit(event_type: str, payload: dict, channel: str = "internal"):
            if emit_event:
                await emit_event(
                    Event(
                        type=event_type,
                        payload=payload,
                        source=agent_source,
                        channel=channel,
                    )
                )

        logger.info("Agent 执行开始", agent_id=self.agent.id, task=task[:50])
        self._set_state(AgentState.RUNNING)
        stream_context = self._stream_context_payload(context_metadata)
        await _emit(
            "agent.thinking",
            {
                "task": task,
                "agent_id": self.agent.id,
                "agent_name": self.agent.name,
                "thinking": self._build_initial_thinking(task),
                **stream_context,
            },
        )

        try:
            result = await self._execute_loop(
                task,
                blackboard_view,
                tool_executor,
                agent_ctx,
                _emit,
                checkpoint,
                context_provider,
                context_metadata,
            )
            self._set_state(AgentState.COMPLETED)
            return result
        except Exception:
            self._set_state(AgentState.FAILED)
            raise

    async def _execute_loop(
        self,
        task: str,
        blackboard_view: dict,
        tool_executor: Optional[ToolExecutor],
        agent_ctx: Optional[AgentContext],
        _emit,
        checkpoint=None,
        context_provider: Optional[AgentContextProvider] = None,
        context_metadata: Optional[dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """内部执行循环（被 run() 和步进模式共用）"""
        # 虚拟工具智能体：跳过 LLM，直接执行工具
        if self.agent.system_prompt == "" and self.agent.role.endswith("_executor"):
            return await self._execute_virtual_agent(task, tool_executor, _emit)

        system_prompt, messages = await self._build_model_context(
            task=task,
            blackboard_view=blackboard_view,
            agent_ctx=agent_ctx,
            context_provider=context_provider,
            context_metadata=context_metadata,
        )

        # 获取可用工具（按 AgentConfig.tools 过滤）
        tools = []
        active_tool_executor = tool_executor
        if tool_executor:
            bind_agent = getattr(tool_executor, "bind_agent", None)
            if callable(bind_agent):
                active_tool_executor = bind_agent(self.agent.id)
            all_tools = await active_tool_executor.list_tools()
            if self.agent.tools:
                allowed = set(self.agent.tools)
                tools = [t for t in all_tools if t.get("function", {}).get("name") in allowed]
            else:
                tools = all_tools
            tools = self._filter_tools_for_task(task, tools)
            logger.debug("Agent 可用工具", agent_id=self.agent.id, tool_count=len(tools))

        # 工具调用循环
        tool_results: List[Dict[str, Any]] = []
        tool_events: List[Dict[str, Any]] = []
        tool_round = 0
        forced_artifact_tool_name: str | None = None
        executed_artifact_tools: set[str] = set()
        stream_message_id = f"stream-{self.agent.id}-{uuid.uuid4().hex[:12]}"

        while tool_round < self.MAX_TOOL_ROUNDS:
            tool_round += 1
            await self._run_checkpoint(checkpoint, "before_llm", {"round": tool_round})

            try:
                tools_for_round = None if self._artifact_tool_succeeded(tool_results) else (tools if tools else None)
                if self.use_streaming:
                    response = await self._chat_streaming(
                        messages=messages,
                        system_prompt=system_prompt,
                        tools=tools_for_round,
                        _emit=_emit,
                        stream_message_id=stream_message_id,
                        stream_context=self._stream_context_payload(context_metadata),
                        defer_stop_if_tool_calls=True,
                    )
                else:
                    response = await collect_chat_stream(
                        self.model,
                        messages=messages,
                        system_prompt=system_prompt,
                        tools=tools_for_round,
                        max_tokens=self.max_output_tokens,
                    )
                self._record_usage(response, system_prompt, messages)
            except Exception as e:
                logger.error("Agent LLM 调用失败", agent_id=self.agent.id, error=str(e))
                raise

            await self._run_checkpoint(
                checkpoint,
                "after_llm",
                {
                    "round": tool_round,
                    "has_tool_calls": bool(response.tool_calls),
                    "content_length": len(response.content or ""),
                },
            )
            content = response.content or ""
            tool_calls = response.tool_calls
            if tool_calls and self._artifact_tool_succeeded(tool_results):
                artifact_tool_name = self._artifact_tool_name(tool_results) or forced_artifact_tool_name
                if self._only_artifact_create_calls(tool_calls):
                    logger.info(
                        "Agent skipped duplicate artifact tool calls",
                        agent_id=self.agent.id,
                        tool=artifact_tool_name,
                    )
                    tool_calls = None
                    if not content.strip() or self._looks_like_artifact_argument_fragment(content):
                        content = self._artifact_completion_message(
                            artifact_tool_name,
                            tool_results,
                            task,
                        )
            if not tool_calls:
                forced_tool_call = self._forced_project_delivery_tool_call(
                    task,
                    tools,
                    tool_results,
                    messages,
                    metadata=context_metadata,
                )
                if forced_tool_call is None and forced_artifact_tool_name is None:
                    forced_tool_call = self._forced_artifact_tool_call(task, tools)
                if forced_tool_call:
                    forced_name = str(forced_tool_call.get("function", {}).get("name") or "")
                    if forced_name.startswith("artifact.create_"):
                        forced_artifact_tool_name = forced_name
                    logger.info(
                        "Agent forced required delivery tool call",
                        agent_id=self.agent.id,
                        tool=forced_name,
                    )
                    content = ""
                    tool_calls = [forced_tool_call]
                elif self._artifact_tool_succeeded(tool_results):
                    if not content.strip() or self._looks_like_artifact_argument_fragment(content):
                        content = self._artifact_completion_message(
                            forced_artifact_tool_name,
                            tool_results,
                            task,
                        )

            if tool_calls:
                tool_calls = self._dedupe_artifact_tool_calls(tool_calls)
                tool_calls = self._drop_executed_artifact_tool_calls(
                    tool_calls,
                    executed_artifact_tools,
                )
                if not tool_calls:
                    logger.info(
                        "Agent skipped already executed artifact tool calls",
                        agent_id=self.agent.id,
                    )
                    tool_calls = None
                    if not content.strip() or self._looks_like_artifact_argument_fragment(content):
                        content = self._artifact_completion_message(
                            forced_artifact_tool_name or self._artifact_tool_name(tool_results),
                            tool_results,
                            task,
                        )

            # 如果没有工具调用，说明 Agent 已完成本轮工作
            if not tool_calls:
                logger.info("Agent 无工具调用", agent_id=self.agent.id)
                messages.append(ChatMessage(role="assistant", content=content))

                break

            tool_calls = self._normalize_tool_calls(tool_calls)

            # 处理工具调用
            logger.info(
                "Agent 工具调用",
                agent_id=self.agent.id,
                tool_count=len(tool_calls),
                round=tool_round,
            )
            await _emit(
                "agent.tool_call",
                {
                    "agent_id": self.agent.id,
                    "agent_message_id": stream_message_id,
                    "tool_count": len(tool_calls),
                    "tools": [tc.get("function", {}).get("name", "unknown") for tc in tool_calls],
                    **self._stream_context_payload(context_metadata),
                },
            )

            # 将 assistant 消息（含 tool_calls）加入消息列表
            assistant_msg = ChatMessage(
                role="assistant",
                content=content,
                tool_calls=tool_calls,
                reasoning_content=response.reasoning_content or None,
            )
            messages.append(assistant_msg)

            # 执行每个工具调用
            for tc in tool_calls:
                if not active_tool_executor:
                    break

                tool_call, err = ToolCall.new(tc)
                await self._run_checkpoint(
                    checkpoint,
                    "before_tool_call",
                    {
                        "round": tool_round,
                        "tool": tc.get("function", {}).get("name", "unknown"),
                        "call_id": getattr(tool_call, "call_id", None),
                    },
                )

                if err:
                    logger.error("工具参数解析失败", tool=tool_call.tool_name, error=str(err))
                    recovered = self._recover_tool_call_arguments(tool_call.tool_name, task)
                    if recovered is not None:
                        logger.info(
                            "Recovered tool arguments with deterministic fallback",
                            agent_id=self.agent.id,
                            tool=tool_call.tool_name,
                        )
                        tool_call.parameters = recovered
                        err = None
                    else:
                        result = ToolResult(
                            call_id=tool_call.call_id,
                            success=False,
                            result=None,
                            error=f"参数解析失败: {err}",
                        )

                if not err:
                    try:
                        logger.info(
                            "执行工具",
                            agent_id=self.agent.id,
                            tool=tool_call.tool_name,
                            call_id=tool_call.call_id,
                        )

                        # 执行工具
                        result = await active_tool_executor.execute(tool_call)

                        # 如果 result 已经是 ToolResult，直接返回
                        if not isinstance(result, ToolResult):
                            # 否则包装为 ToolResult
                            result = ToolResult(
                                call_id=tool_call.call_id,
                                success=True,
                                result=result,
                            )
                    except Exception as e:
                        logger.error("工具执行失败", tool=tool_call.tool_name, error=str(e))

                        result = ToolResult(
                            call_id=tool_call.call_id,
                            success=False,
                            result=None,
                            error=str(e),
                        )

                tool_name = tc.get("function", {}).get("name", "unknown")
                if (
                    str(tool_name).startswith("artifact.create_")
                    and isinstance(result, ToolResult)
                    and result.success
                ):
                    executed_artifact_tools.add(str(tool_name))
                    forced_artifact_tool_name = str(tool_name)
                tool_results.append(
                    {
                        "call_id": tool_call.call_id,
                        "tool": tool_name,
                        "success": result.success if isinstance(result, ToolResult) else True,
                        "error": result.error if isinstance(result, ToolResult) else None,
                        "result": result.result if isinstance(result, ToolResult) else result,
                    }
                )

                await _emit(
                    "agent.tool_result",
                    {
                        "agent_id": self.agent.id,
                        "agent_message_id": stream_message_id,
                        "tool": tool_name,
                        "success": result.success if isinstance(result, ToolResult) else True,
                        "result": result.result if isinstance(result, ToolResult) else result,
                        "error": result.error if isinstance(result, ToolResult) else None,
                        **self._stream_context_payload(context_metadata),
                    },
                )
                await self._run_checkpoint(
                    checkpoint,
                    "after_tool_call",
                    {
                        "round": tool_round,
                        "tool": tool_name,
                        "call_id": tool_call.call_id,
                        "success": result.success if isinstance(result, ToolResult) else True,
                    },
                )

                # 将工具结果加入消息列表
                tool_msg = ChatMessage(
                    role="tool",
                    content=str(result.result if isinstance(result, ToolResult) else result),
                    name=tool_name,
                    tool_call_id=tool_call.call_id,
                )
                messages.append(tool_msg)

            tool_events.append(
                {
                    "agent_id": self.agent.id,
                    "round": tool_round,
                    "results": tool_results[-len(tool_calls) :],
                }
            )

            if self._artifact_tool_succeeded(tool_results):
                logger.info(
                    "Agent artifact tool succeeded; requesting final answer from model",
                    agent_id=self.agent.id,
                    tool=self._artifact_tool_name(tool_results) or forced_artifact_tool_name,
                    round=tool_round,
                )
            await self._run_checkpoint(
                checkpoint,
                "after_tool_round",
                {"round": tool_round, "tool_count": len(tool_calls)},
            )

        # 如果达到工具调用上限
        if tool_round >= self.MAX_TOOL_ROUNDS:
            logger.warning(
                "Agent 工具调用达到上限",
                agent_id=self.agent.id,
                max_rounds=self.MAX_TOOL_ROUNDS,
            )

        # 解析回复：分离成果和状态报告
        final_content = messages[-1].content if messages else ""
        # 如果最后一条消息是 tool 消息，需要再调一次 LLM 获取总结
        if messages and messages[-1].role == "tool":
            try:
                await self._run_checkpoint(checkpoint, "before_summary", {})
                summary_messages = [
                    *messages,
                    ChatMessage(
                        role="user",
                        content=self._summary_instruction(tool_results),
                    ),
                ]
                if self.use_streaming:
                    summary_response = await self._chat_streaming(
                        messages=summary_messages,
                        system_prompt=system_prompt,
                        tools=None,
                        _emit=_emit,
                        stream_message_id=stream_message_id,
                        stream_context=self._stream_context_payload(context_metadata),
                    )
                else:
                    summary_response = await collect_chat_stream(
                        self.model,
                        messages=summary_messages,
                        system_prompt=system_prompt,
                    )
                final_content = summary_response.content or ""
                await self._run_checkpoint(
                    checkpoint,
                    "after_summary",
                    {"content_length": len(final_content)},
                )
            except Exception as e:
                logger.error("Agent 总结调用失败", agent_id=self.agent.id, error=str(e))
                final_content = "工具调用完成，但总结失败。"

        status_report = self._extract_status_report(final_content)
        work_product = self._remove_status_report(final_content)
        if self._artifact_tool_succeeded(tool_results) and (
            not work_product.strip() or self._looks_like_artifact_argument_fragment(work_product)
        ):
            work_product = self._artifact_completion_message(
                self._artifact_tool_name(tool_results) or forced_artifact_tool_name,
                tool_results,
                task,
            )
            if self.use_streaming:
                await self._emit_text_response(
                    _emit,
                    stream_message_id,
                    work_product,
                    stream_context=self._stream_context_payload(context_metadata),
                )
        elif not work_product.strip() and self._has_failed_tool_results(tool_results):
            work_product = self._tool_failure_message(tool_results)
            if status_report.state != AgentState.FAILED:
                status_report = AgentReport(
                    agent_id=status_report.agent_id,
                    state=AgentState.FAILED,
                    will=AgentWill.BLOCKED,
                    target_task=status_report.target_task,
                    blockers=[work_product],
                    priority=status_report.priority,
                    confidence=0.0,
                    rationale=status_report.rationale
                    or "Tool execution failed and no user-visible work product was produced.",
                    expected_duration=status_report.expected_duration,
                )
            if self.use_streaming:
                await self._emit_text_response(
                    _emit,
                    stream_message_id,
                    work_product,
                    stream_context=self._stream_context_payload(context_metadata),
                )
        if (
            self._artifact_tool_succeeded(tool_results)
            and status_report.state == AgentState.UNKNOWN
            and work_product.strip()
        ):
            status_report = AgentReport(
                agent_id=status_report.agent_id,
                state=AgentState.COMPLETED,
                will=AgentWill.COMPLETE,
                target_task=status_report.target_task,
                blockers=status_report.blockers,
                priority=status_report.priority,
                confidence=max(status_report.confidence, 0.9),
                rationale=status_report.rationale or "Artifact tool succeeded.",
                expected_duration=status_report.expected_duration,
            )
        runtime_failure = self._runtime_validation_failure_message(tool_results)
        project_runtime_failure = self._project_runtime_validation_failure(task, tool_results)
        if project_runtime_failure:
            runtime_failure = (
                f"{runtime_failure}；{project_runtime_failure}"
                if runtime_failure
                else project_runtime_failure
            )
        if runtime_failure and status_report.state == AgentState.COMPLETED:
            if work_product.strip():
                work_product = f"{work_product.rstrip()}\n\n注意：{runtime_failure}"
            else:
                work_product = runtime_failure
            status_report = AgentReport(
                agent_id=status_report.agent_id,
                state=AgentState.FAILED,
                will=AgentWill.BLOCKED,
                target_task=status_report.target_task,
                blockers=[runtime_failure],
                priority=status_report.priority,
                confidence=0.0,
                rationale="Runtime validation tools failed; successful file/artifact creation alone does not prove the project ran.",
                expected_duration=status_report.expected_duration,
            )

        # 将本轮对话归档回 AgentContext
        missing_delivery = self._project_delivery_missing_result(task, tool_results)
        if missing_delivery:
            work_product = missing_delivery
            status_report = AgentReport(
                agent_id=status_report.agent_id,
                state=AgentState.FAILED,
                will=AgentWill.BLOCKED,
                target_task=status_report.target_task,
                blockers=[missing_delivery],
                priority=status_report.priority,
                confidence=0.0,
                rationale="Project delivery requires persisted tool results; narration alone is not accepted.",
                expected_duration=status_report.expected_duration,
            )
            if self.use_streaming:
                await self._emit_text_response(
                    _emit,
                    stream_message_id,
                    work_product,
                    stream_context=self._stream_context_payload(context_metadata),
                )

        if agent_ctx:
            agent_ctx.add("thought", work_product)
            if status_report.rationale:
                agent_ctx.add("thought", status_report.rationale)
            for te in tool_events:
                for r in te.get("results", []):
                    agent_ctx.add("tool_result", r)

        logger.info(
            "Agent 执行完成",
            agent_id=self.agent.id,
            state=status_report.state,
            will=status_report.will.value,
            confidence=status_report.confidence,
            tool_rounds=tool_round,
        )

        return {
            "work_product": work_product,
            "status_report": status_report,
            "tool_events": tool_events,
            "stream_message_id": stream_message_id,
            "usage": dict(self.usage),
            "usage_estimated": self.usage_estimated,
        }

    def _record_usage(
        self,
        response: ChatResponse,
        system_prompt: str,
        messages: list[ChatMessage],
    ) -> None:
        usage = response.usage or {}
        if usage:
            self.usage["prompt_tokens"] += int(usage.get("prompt_tokens") or 0)
            self.usage["completion_tokens"] += int(usage.get("completion_tokens") or 0)
            return
        prompt = system_prompt + "\n" + "\n".join(message.content or "" for message in messages)
        self.usage["prompt_tokens"] += self._estimate_tokens(prompt)
        self.usage["completion_tokens"] += self._estimate_tokens(response.content or "")
        self.usage_estimated = True

    def _estimate_tokens(self, text: str) -> int:
        counter = getattr(self.model, "count_tokens", None)
        if callable(counter):
            return max(0, int(counter(text)))
        cn_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        en_words = len(re.findall(r"[a-zA-Z]+", text))
        return cn_chars + int(en_words * 1.3) + 10

    @staticmethod
    async def _run_checkpoint(checkpoint, stage: str, payload: Dict[str, Any]) -> None:
        """Yield control and let AgentStepper inspect pending control events."""
        await asyncio.sleep(0)
        if checkpoint:
            await checkpoint(stage, payload)

    @staticmethod
    def _stream_context_payload(
        context_metadata: Optional[dict[str, Any]],
    ) -> dict[str, str]:
        metadata = context_metadata or {}
        payload: dict[str, str] = {}
        for key in ("conversation_id", "user_message_id", "client_message_id"):
            value = str(metadata.get(key) or "").strip()
            if value:
                payload[key] = value
        return payload

    @staticmethod
    def _artifact_tool_succeeded(tool_results: List[Dict[str, Any]]) -> bool:
        return any(
            str(result.get("tool", "")).startswith("artifact.create_")
            and result.get("success") is True
            for result in tool_results
        )

    @staticmethod
    def _artifact_tool_name(tool_results: List[Dict[str, Any]]) -> str | None:
        for result in reversed(tool_results):
            tool_name = str(result.get("tool") or "")
            if tool_name.startswith("artifact.create_") and result.get("success") is True:
                return tool_name
        return None

    @staticmethod
    def _has_failed_tool_results(tool_results: List[Dict[str, Any]]) -> bool:
        return any(result.get("success") is False for result in tool_results)

    @staticmethod
    def _tool_failure_message(tool_results: List[Dict[str, Any]]) -> str:
        failures = [
            result
            for result in tool_results
            if result.get("success") is False
        ]
        if not failures:
            return "工具执行没有产出可见结果，需要重新执行或人工复核。"
        first = failures[0]
        tool = str(first.get("tool") or first.get("tool_name") or "工具")
        error = str(first.get("error") or "执行失败").strip()
        total = len(failures)
        suffix = f"；本轮共有 {total} 个工具调用失败。" if total > 1 else ""
        return f"{tool} 执行失败，未生成可交付产物：{error}{suffix}"

    @classmethod
    def _summary_instruction(cls, tool_results: List[Dict[str, Any]]) -> str:
        deploy_success = cls._latest_successful_deployment_output(tool_results)
        if deploy_success:
            deployment_url = str(
                deploy_success.get("url") or deploy_success.get("public_url") or ""
            ).strip()
            backend_path = str(
                deploy_success.get("validated_backend_path")
                or deploy_success.get("backend_health_url")
                or ""
            ).strip()
            return (
                "deploy.preview 已经成功创建真实部署。请只依据下面这些工具返回的事实回复用户，"
                "不要手写、改写或重新拼接 deployment_id / artifact_id / URL；必须原样复制部署地址。"
                "不要声明未被工具明确验证的接口、CRUD 流程或健康检查。"
                f"\n部署地址：{deployment_url}"
                f"\n后端验证路径：{backend_path or '以 deploy.preview health checks 为准'}"
            )
        instruction = (
            "工具已经执行完成。请用自然中文给用户一个简洁最终回复，"
            "说明你完成了什么，以及用户可以通过产物卡片预览或下载。"
            "不要再次调用工具，不要输出 JSON 或代码块。"
        )
        runtime_failure = cls._runtime_validation_failure_message(tool_results)
        if runtime_failure:
            instruction += (
                "\n\n注意：本轮存在运行、测试、部署或终端工具失败。最终回复必须如实说明失败项和剩余风险；"
                "不得声称依赖已安装、服务已启动、测试通过、健康检查通过或部署成功，除非对应工具结果明确成功。"
                f"\n失败摘要：{runtime_failure}"
            )
        return instruction

    @staticmethod
    def _runtime_validation_failure_message(tool_results: List[Dict[str, Any]]) -> str | None:
        runtime_tools = {
            "sandbox.run",
            "terminal.start",
            "terminal.send",
            "terminal.wait_for",
            "terminal.stop",
            "api.test",
            "browser.open",
            "deploy.preview",
            "test.run",
        }
        failures: list[str] = []
        deploy_succeeded = AgentLoop._tool_succeeded(tool_results, "deploy.preview")
        successful_runtime_validation = AgentLoop._has_successful_non_install_runtime_validation(tool_results)
        for result in tool_results:
            tool = str(result.get("tool") or result.get("tool_name") or "")
            if tool not in runtime_tools:
                continue
            if deploy_succeeded and tool.startswith("terminal."):
                continue
            command = AgentLoop._runtime_command(result)
            failed = result.get("success") is False
            output = result.get("result")
            if isinstance(output, dict):
                status = str(output.get("status") or "").lower()
                failed = failed or status in {"failed", "error", "timeout"}
                nested = output.get("output")
                if isinstance(nested, dict):
                    nested_status = str(nested.get("status") or "").lower()
                    failed = failed or nested_status in {"failed", "error", "timeout"}
                    if nested.get("assertion_passed") is False:
                        failed = True
            if not failed:
                continue
            if successful_runtime_validation and AgentLoop._is_package_install_command(command):
                continue
            error = str(result.get("error") or "").strip()
            if not error and isinstance(output, dict):
                error = str(output.get("error") or output.get("message") or "").strip()
                nested = output.get("output")
                if not error and isinstance(nested, dict):
                    error = str(
                        nested.get("error")
                        or nested.get("stderr")
                        or nested.get("response_summary")
                        or nested.get("message")
                        or ""
                    ).strip()
            failures.append(f"{tool}: {error or '执行结果未通过'}")
        if not failures:
            return None
        suffix = f"；另有 {len(failures) - 1} 个运行/验证工具失败" if len(failures) > 1 else ""
        return f"{failures[0]}{suffix}"

    @staticmethod
    def _latest_successful_deployment_output(tool_results: List[Dict[str, Any]]) -> Dict[str, Any]:
        for item in reversed(tool_results):
            if str(item.get("tool") or item.get("tool_name") or "") != "deploy.preview":
                continue
            if item.get("success") is not True:
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            output = result.get("output") if isinstance(result.get("output"), dict) else None
            candidate = output if isinstance(output, dict) else result
            if str(candidate.get("status") or "").lower() in {"failed", "error", "timeout"}:
                continue
            if candidate.get("url") or candidate.get("public_url"):
                return candidate
        return {}

    @staticmethod
    def _has_successful_non_install_runtime_validation(tool_results: List[Dict[str, Any]]) -> bool:
        validation_tools = {"sandbox.run", "terminal.start", "api.test", "browser.open", "deploy.preview", "test.run"}
        for result in tool_results:
            tool = str(result.get("tool") or result.get("tool_name") or "")
            if tool not in validation_tools or AgentLoop._is_package_install_command(AgentLoop._runtime_command(result)):
                continue
            if result.get("success") is False:
                continue
            output = result.get("result")
            if isinstance(output, dict):
                statuses = [str(output.get("status") or "").lower()]
                nested = output.get("output")
                if isinstance(nested, dict):
                    statuses.append(str(nested.get("status") or "").lower())
                    if nested.get("assertion_passed") is False:
                        continue
                if any(status in {"failed", "error", "timeout"} for status in statuses):
                    continue
            return True
        return False

    @staticmethod
    def _runtime_command(result: Dict[str, Any]) -> str:
        args = result.get("arguments") if isinstance(result.get("arguments"), dict) else None
        command = str((args or {}).get("command") or "").strip()
        output = result.get("result")
        if not command and isinstance(output, dict):
            nested = output.get("output")
            if isinstance(nested, dict):
                command = str(nested.get("command") or "").strip()
        return command

    @staticmethod
    def _project_runtime_validation_failure(
        task: str,
        tool_results: List[Dict[str, Any]],
    ) -> str | None:
        if not AgentLoop._looks_like_project_code_delivery(task):
            return None
        for result in tool_results:
            if str(result.get("tool") or result.get("tool_name") or "") != "api.test":
                continue
            output = result.get("result")
            if isinstance(output, dict) and isinstance(output.get("output"), dict):
                output = output["output"]
            if not isinstance(output, dict):
                continue
            if output.get("is_platform_app_probe") is True:
                return "api.test tested AgentHub's own app, not the generated backend service"
        return None

    @staticmethod
    def _is_package_install_command(command: str) -> bool:
        normalized = " ".join(str(command or "").strip().lower().split())
        return bool(
            normalized.startswith("pip install ")
            or normalized.startswith("pip3 install ")
            or normalized.startswith("python -m pip install ")
            or normalized.startswith("py -m pip install ")
        )

    @staticmethod
    def _only_artifact_create_calls(tool_calls: List[Dict[str, Any]]) -> bool:
        if not tool_calls:
            return False
        for tool_call in tool_calls:
            name = str(tool_call.get("function", {}).get("name") or "")
            if not name.startswith("artifact.create_"):
                return False
        return True

    @staticmethod
    def _dedupe_artifact_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen_artifact_tools: set[str] = set()
        deduped: List[Dict[str, Any]] = []
        for tool_call in tool_calls:
            name = str(tool_call.get("function", {}).get("name") or "")
            if name.startswith("artifact.create_"):
                if name in seen_artifact_tools:
                    continue
                seen_artifact_tools.add(name)
            deduped.append(tool_call)
        return deduped

    @staticmethod
    def _drop_executed_artifact_tool_calls(
        tool_calls: List[Dict[str, Any]],
        executed_artifact_tools: set[str],
    ) -> List[Dict[str, Any]]:
        if not executed_artifact_tools:
            return tool_calls
        return [
            tool_call
            for tool_call in tool_calls
            if str(tool_call.get("function", {}).get("name") or "") not in executed_artifact_tools
        ]

    @staticmethod
    def _normalize_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        normalized: List[Dict[str, Any]] = []
        for index, tool_call in enumerate(tool_calls):
            item = dict(tool_call or {})
            item.setdefault("type", "function")
            function_info = dict(item.get("function") or {})
            item["function"] = function_info
            if not item.get("id"):
                tool_name = str(function_info.get("name") or "tool").replace(".", "_")
                item["id"] = f"call_{tool_name}_{index}_{uuid.uuid4().hex[:8]}"
            normalized.append(item)
        return normalized

    @staticmethod
    def _looks_like_artifact_argument_fragment(content: str) -> bool:
        normalized = content.strip().lower()
        if normalized in {"0", ">", "<", "li", "ul", "html", "body", "head", "script"}:
            return True
        return bool(len(normalized) <= 3 and re.fullmatch(r"[<>/a-z0-9]+", normalized))

    @staticmethod
    def _artifact_completion_message(
        tool_name: str | None,
        tool_results: List[Dict[str, Any]] | None = None,
        task: str = "",
    ) -> str:
        label = {
            "artifact.create_pdf": "PDF",
            "artifact.create_docx": "Word",
            "artifact.create_pptx": "PPT",
            "artifact.create_xlsx": "Excel",
            "artifact.create_html": "HTML",
            "artifact.create_web_app": "HTML",
        }.get(tool_name or "", "产物")
        output = AgentLoop._latest_artifact_output(tool_results or [], tool_name)
        title = AgentLoop._artifact_display_title(output, task)
        title_part = f"《{title}》" if title else ""
        if label == "HTML":
            return (
                f"我已经把{title_part or '你要的页面'}做成可运行的 HTML 页面了，"
                "可以在下面的产物卡片里直接预览运行效果，也可以下载源文件继续修改。"
            )
        if label == "Excel":
            return (
                f"我已经为你生成好{title_part} Excel 表格了，"
                "可以在下面的产物卡片里预览并下载真实文件。"
            )
        if label == "PPT":
            return (
                f"我已经为你生成好{title_part} PPT 演示文稿了，"
                "可以在下面的产物卡片里预览并下载真实文件。"
            )
        if label in {"PDF", "Word"}:
            return (
                f"我已经为你生成好{title_part} {label} 文档了，"
                "可以在下面的产物卡片里预览排版效果，也可以直接下载。"
            )
        return "我已经完成产物生成，可以在下面的产物卡片里预览和下载。"

    @staticmethod
    def _latest_artifact_output(
        tool_results: List[Dict[str, Any]],
        tool_name: str | None,
    ) -> Dict[str, Any]:
        for item in reversed(tool_results):
            item_tool = str(item.get("tool") or item.get("tool_name") or "")
            if tool_name and item_tool != tool_name:
                continue
            if not item_tool.startswith("artifact.create_"):
                continue
            result = item.get("result") if isinstance(item.get("result"), dict) else {}
            output = result.get("output") if isinstance(result.get("output"), dict) else None
            if isinstance(output, dict):
                return output
            if isinstance(result, dict):
                return result
        return {}

    @staticmethod
    def _artifact_display_title(output: Dict[str, Any], task: str = "") -> str:
        artifact = output.get("artifact") if isinstance(output.get("artifact"), dict) else {}
        raw_title = (
            output.get("title")
            or output.get("name")
            or artifact.get("name")
            or artifact.get("title")
            or output.get("filename")
            or ""
        )
        title = str(raw_title).strip()
        if "." in title:
            title = title.rsplit(".", 1)[0]
        if not title and task:
            title = AgentLoop._title_from_task(task)
        return title[:80]

    async def _emit_text_response(
        self,
        _emit,
        stream_message_id: str,
        text: str,
        stream_context: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not text.strip():
            return
        stream_context = stream_context or {}
        await _emit(
            "message_start",
            {
                "agent_id": self.agent.id,
                "agent_name": self.agent.name,
                "agent_avatar_url": (self.agent.model_config or {}).get("avatar_url"),
                "agent_message_id": stream_message_id,
                **stream_context,
            },
        )
        for index in range(0, len(text), 12):
            await _emit(
                "agent.token",
                {
                    "agent_id": self.agent.id,
                    "agent_name": self.agent.name,
                    "agent_avatar_url": (self.agent.model_config or {}).get("avatar_url"),
                    "agent_message_id": stream_message_id,
                    "token": text[index : index + 12],
                    **stream_context,
                },
            )
            await asyncio.sleep(0)
        await _emit(
            "message_stop",
            {
                "agent_id": self.agent.id,
                "agent_name": self.agent.name,
                "agent_message_id": stream_message_id,
                **stream_context,
            },
        )

    @staticmethod
    def _title_from_task(task: str) -> str:
        title = re.sub(r"\s+", " ", task or "").strip()
        title = re.sub(r"^(请|帮我|帮忙|麻烦)?(生成|创建|做|制作)(一个|一份|一下)?", "", title)
        title = re.sub(r"(pdf|word|docx|pptx?|excel|xlsx|html|网页|页面|文档)$", "", title, flags=re.IGNORECASE)
        return title.strip(" ：:，,。")[:40]

    def _forced_artifact_tool_call(
        self,
        task: str,
        tools: List[Dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not tools:
            return None
        if self._looks_like_project_code_delivery(task):
            return None
        try:
            from app.services.llm.tool_calls import artifact_arguments, detect_artifact_tool
        except Exception:
            return None
        available = {
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        }
        requested = detect_artifact_tool(task)
        if (
            requested == "artifact.create_html"
            and requested not in available
            and "artifact.create_web_app" in available
        ):
            requested = "artifact.create_web_app"
        if not requested or requested not in available:
            return None
        return {
            "id": f"call_forced_{requested.replace('.', '_')}_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": requested,
                "arguments": json.dumps(artifact_arguments(requested, task), ensure_ascii=False),
            },
        }

    def _forced_project_delivery_tool_call(
        self,
        task: str,
        tools: List[Dict[str, Any]],
        tool_results: List[Dict[str, Any]],
        messages: List[ChatMessage] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Enforce real workspace/code delivery for project-building turns.

        The scheduler can ask Backend/Frontend/Deploy agents to build an actual
        project. If the model only narrates completion, force the minimum real
        tool action required for that role instead of accepting a hallucinated
        report.
        """

        if not tools or not self._looks_like_project_code_delivery(task):
            return None
        available = {
            str(tool.get("function", {}).get("name") or "")
            for tool in tools
            if isinstance(tool, dict)
        }
        role = f"{self.agent.name} {self.agent.role}".lower()
        is_release = any(token in role for token in ("deploy", "release", "ops", "部署", "发布", "上线"))
        if is_release and "deploy.preview" in available and not self._tool_succeeded(tool_results, "deploy.preview"):
            artifact_id = self._latest_artifact_id(tool_results, messages or [], task)
            if artifact_id:
                deploy_args: dict[str, Any] = {
                    "artifact_id": artifact_id,
                    "mode": "preview_link",
                }
                # 传递 conversation_id 以支持全栈部署（检测后端工作区文件）
                if metadata:
                    conv_id = str(metadata.get("conversation_id") or metadata.get("session_id") or "")
                    if conv_id:
                        deploy_args["conversation_id"] = conv_id
                return self._tool_call(
                    "deploy.preview",
                    deploy_args,
                    prefix="project_deploy",
                )
        return None

    @staticmethod
    def _latest_artifact_id(
        tool_results: List[Dict[str, Any]],
        messages: List[ChatMessage],
        task: str,
    ) -> str | None:
        for item in reversed(tool_results):
            result = item.get("result")
            if isinstance(result, dict):
                artifact_id = result.get("artifact_id") or (result.get("artifact") or {}).get("id")
                if artifact_id:
                    return str(artifact_id)
        text_parts = [task or ""]
        for message in reversed(messages[-12:]):
            text_parts.append(str(getattr(message, "content", "") or ""))
        haystack = "\n".join(text_parts)
        patterns = (
            r"artifact_id\s*[:=]\s*['\"]?([0-9a-fA-F-]{16,})",
            r'"artifact_id"\s*:\s*"([0-9a-fA-F-]{16,})"',
            r"/artifacts/([0-9a-fA-F-]{16,})/",
        )
        for pattern in patterns:
            matches = re.findall(pattern, haystack)
            if matches:
                return str(matches[-1])
        return None

    @staticmethod
    def _tool_succeeded(tool_results: List[Dict[str, Any]], tool_name: str) -> bool:
        return any(
            str(result.get("tool") or "") == tool_name and result.get("success") is True
            for result in tool_results
        )

    def _project_delivery_missing_result(
        self,
        task: str,
        tool_results: List[Dict[str, Any]],
    ) -> str | None:
        if not self._looks_like_project_code_delivery(task):
            return None
        role = f"{self.agent.name} {self.agent.role}".lower()
        is_backend = any(token in role for token in ("back", "backend", "api", "server", "后端", "服务端", "接口"))
        is_frontend = any(token in role for token in ("front", "frontend", "ui", "web", "前端", "页面"))
        is_release = any(token in role for token in ("deploy", "release", "ops", "部署", "发布", "上线"))
        has_file_write = self._tool_succeeded(tool_results, "file.write")
        has_artifact = self._artifact_tool_succeeded(tool_results)
        has_deploy = self._tool_succeeded(tool_results, "deploy.preview")
        has_run = self._tool_succeeded(tool_results, "sandbox.run")
        has_terminal = any(
            str(result.get("tool") or "").startswith("terminal.") and result.get("success") is True
            for result in tool_results
        )
        if is_backend and not has_file_write:
            return "后端项目交付需要通过 file.write 写入真实后端代码文件；本轮没有检测到文件写入结果，已拒绝口头完成。"
        if is_frontend and not (has_file_write or has_artifact):
            return "前端项目交付需要写入真实前端文件，或生成真实可运行的 HTML/Web 产物；本轮没有检测到真实文件或产物，已拒绝口头完成。"
        if is_release and not (has_deploy or has_run or has_terminal):
            return "部署交付需要真实部署预览、沙箱运行或终端服务验证结果；本轮没有检测到成功的部署/运行记录，已拒绝口头完成。"
        return None

    @staticmethod
    def _tool_call(tool_name: str, arguments: dict[str, Any], *, prefix: str) -> dict[str, Any]:
        return {
            "id": f"call_forced_{prefix}_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": tool_name,
                "arguments": json.dumps(arguments, ensure_ascii=False),
            },
        }

    @staticmethod
    def _project_slug(task: str) -> str:
        text = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", str(task or "").lower()).strip("-")
        if not text:
            return "agenthub-project"
        if any(word in text for word in ("国际象棋", "chess")):
            return "classical-chess"
        if any(word in text for word in ("五子棋", "gomoku")):
            return "gomoku-project"
        return text[:48].strip("-") or "agenthub-project"

    def _filter_tools_for_task(
        self,
        task: str,
        tools: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Narrow visible tools for project-code delivery turns.

        Agents may have broad permissions, but a frontend/backend project task should not
        accidentally become a PDF/Word/PPT artifact just because those tools are also
        authorized. The scheduler still decides who acts; this only removes misleading
        document tools from the current role's tool view.
        """

        if not self._looks_like_project_code_delivery(task):
            return tools
        role = f"{self.agent.name} {self.agent.role}".lower()
        is_frontend = any(token in role for token in ("front", "frontend", "ui", "ux", "前端", "页面", "界面"))
        is_backend = any(token in role for token in ("back", "backend", "api", "server", "后端", "服务端", "接口"))
        is_release = any(token in role for token in ("deploy", "release", "ops", "部署", "发布", "上线"))
        if not (is_frontend or is_backend or is_release):
            return tools

        blocked_document_tools = {
            "artifact.create_pdf",
            "artifact.create_docx",
            "artifact.create_pptx",
            "artifact.create_xlsx",
        }
        filtered: List[Dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("function", {}).get("name") or "") if isinstance(tool, dict) else ""
            if name in blocked_document_tools:
                continue
            if is_backend and name.startswith("artifact.create_"):
                continue
            if is_backend and name == "api.test":
                continue
            if is_release and name.startswith("terminal."):
                continue
            if is_release and name == "sandbox.run":
                continue
            filtered.append(tool)
        return filtered

    @staticmethod
    def _looks_like_project_code_delivery(task: str) -> bool:
        normalized = "".join(str(task or "").lower().split())
        if not normalized:
            return False
        project_markers = (
            "前后端",
            "前端后端",
            "前端",
            "后端",
            "api",
            "接口",
            "应用",
            "项目",
            "游戏",
            "网页",
            "页面",
            "工作区",
            "文件夹",
            "代码",
            "试一下",
            "体验",
            "部署",
            "project",
            "app",
            "game",
            "frontend",
            "backend",
            "web",
            "deploy",
        )
        doc_only_markers = ("说明文档", "pdf说明", "报告", "文档")
        has_project = any(marker in normalized for marker in project_markers)
        has_code_or_delivery = any(
            marker in normalized
            for marker in (
                "前端",
                "后端",
                "api",
                "接口",
                "代码",
                "工作区",
                "文件夹",
                "试一下",
                "体验",
                "部署",
                "frontend",
                "backend",
                "web",
                "deploy",
            )
        )
        return has_project and (has_code_or_delivery or not any(marker in normalized for marker in doc_only_markers))

    @staticmethod
    def _recover_tool_call_arguments(tool_name: str, task: str) -> dict[str, Any] | None:
        try:
            from app.services.llm.html_artifacts import HTML_ARTIFACT_TOOLS
            from app.services.llm.tool_calls import artifact_arguments
        except Exception:
            return None
        if tool_name not in HTML_ARTIFACT_TOOLS:
            return None
        return artifact_arguments(tool_name, task)

    async def _execute_virtual_agent(
        self,
        task: str,
        tool_executor: Optional[ToolExecutor],
        _emit,
    ) -> Dict[str, Any]:
        """虚拟工具智能体执行：跳过 LLM，直接调用工具。

        从 model_config 读取节点配置，构造 ToolCall 并执行。
        """
        node_config = self.agent.model_config.get("node_config", {})
        node_type = self.agent.model_config.get("node_type", "")

        tool_name = ""
        arguments: Dict[str, Any] = {}

        if node_type == "tool":
            tool_name = node_config.get("tool_name", "") or (self.agent.tools[0] if self.agent.tools else "")
            arguments = node_config.get("arguments", {})
        elif node_type == "mcp":
            tool_name = node_config.get("tool_name", "")
            arguments = node_config.get("arguments", {})
        else:
            # skill / artifact 暂走 LLM 路径（在 _execute_loop 中不应走到这里）
            logger.warning("虚拟智能体类型不支持直接执行", agent_id=self.agent.id, node_type=node_type)
            return {
                "work_product": f"虚拟智能体 {self.agent.name} 暂不支持直接执行",
                "status_report": AgentReport(
                    agent_id=self.agent.id,
                    state=AgentState.COMPLETED,
                    will=AgentWill.COMPLETE,
                    rationale=f"节点类型 {node_type} 不走虚拟执行路径",
                    confidence=1.0,
                ),
                "tool_events": [],
            }

        if not tool_name or not tool_executor:
            return {
                "work_product": "工具执行失败：未配置工具名或执行器",
                "status_report": AgentReport(
                    agent_id=self.agent.id,
                    state=AgentState.FAILED,
                    will=AgentWill.BLOCKED,
                    rationale="缺少工具名或工具执行器",
                    confidence=1.0,
                ),
                "tool_events": [],
            }

        logger.info("虚拟智能体直接执行工具", agent_id=self.agent.id, tool=tool_name)
        await _emit(
            "agent.tool_call",
            {
                "agent_id": self.agent.id,
                "tool_count": 1,
                "tools": [tool_name],
            },
        )

        # 构造 ToolCall 并执行
        tool_call = ToolCall(
            call_id=f"virtual_{self.agent.id}_{uuid.uuid4().hex[:8]}",
            tool_name=tool_name,
            parameters=arguments,
        )

        try:
            bound_executor = tool_executor
            bind_agent = getattr(tool_executor, "bind_agent", None)
            if callable(bind_agent):
                bound_executor = bind_agent(self.agent.id)
            result = await bound_executor.execute(tool_call)
            if not isinstance(result, ToolResult):
                result = ToolResult(
                    call_id=tool_call.call_id,
                    success=True,
                    result=result,
                )
        except Exception as e:
            logger.error("虚拟智能体工具执行失败", agent_id=self.agent.id, tool=tool_name, error=str(e))
            result = ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=str(e),
            )

        await _emit(
            "agent.tool_result",
            {
                "agent_id": self.agent.id,
                "tool": tool_name,
                "success": result.success,
                "result": result.result,
                "error": result.error,
            },
        )

        tool_events = [
            {
                "agent_id": self.agent.id,
                "round": 1,
                "results": [
                    {
                        "tool": tool_name,
                        "success": result.success,
                        "result": result.result,
                    }
                ],
            }
        ]

        work_product = str(result.result) if result.success else f"工具执行失败: {result.error}"

        return {
            "work_product": work_product,
            "status_report": AgentReport(
                agent_id=self.agent.id,
                state=AgentState.COMPLETED if result.success else AgentState.FAILED,
                will=AgentWill.COMPLETE if result.success else AgentWill.BLOCKED,
                rationale=f"工具 {tool_name} 执行{'成功' if result.success else '失败'}",
                confidence=1.0,
            ),
            "tool_events": tool_events,
        }

    async def _chat_streaming(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str],
        tools: Optional[List[Dict]],
        _emit,
        stream_message_id: str,
        stream_context: Optional[Dict[str, Any]] = None,
        defer_stop_if_tool_calls: bool = False,
    ) -> ChatResponse:
        """流式对话，emit agent.token 事件，组装成 ChatResponse"""
        content_parts: List[str] = []
        reasoning_parts: List[str] = []
        token_filter = _StatusReportStreamFilter()
        stream_started = False
        stream_context = stream_context or {}

        async def ensure_stream_started() -> None:
            nonlocal stream_started
            if stream_started:
                return
            stream_started = True
            await _emit(
                "message_start",
                {
                    "agent_id": self.agent.id,
                    "agent_name": self.agent.name,
                    "agent_avatar_url": (self.agent.model_config or {}).get("avatar_url"),
                    "agent_message_id": stream_message_id,
                    **stream_context,
                },
            )
        # tool_calls 在流式中可能分散在多个 chunk，需要积累
        tool_calls_acc: Dict[int, Dict[str, Any]] = {}

        stream = self.model.chat_stream(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            max_tokens=self.max_output_tokens,
        )

        async for chunk in stream:
            # 1. 文本内容
            if chunk.content:
                content_parts.append(chunk.content)
                visible_delta = token_filter.push(chunk.content)
                if visible_delta:
                    await ensure_stream_started()
                    await _emit(
                        "agent.token",
                        {
                            "agent_id": self.agent.id,
                            "agent_name": self.agent.name,
                            "agent_avatar_url": (self.agent.model_config or {}).get("avatar_url"),
                            "agent_message_id": stream_message_id,
                            "token": visible_delta,
                            **stream_context,
                        },
                    )

            # 1.5 思考过程
            if chunk.reasoning:
                reasoning_parts.append(chunk.reasoning)
                await ensure_stream_started()
                await _emit(
                    "agent.thinking",
                        {
                            "agent_id": self.agent.id,
                            "agent_name": self.agent.name,
                            "agent_avatar_url": (self.agent.model_config or {}).get("avatar_url"),
                            "agent_message_id": stream_message_id,
                            "thinking": chunk.reasoning,
                            **stream_context,
                        },
                )

            # 2. tool_call 增量（可能跨多个 chunk）
            if chunk.tool_call:
                tc = chunk.tool_call
                idx = tc.get("index", 0)
                if idx not in tool_calls_acc:
                    tool_calls_acc[idx] = tc
                else:
                    # 合并 arguments（增量追加）
                    existing = tool_calls_acc[idx]
                    if "function" in tc and "function" in existing:
                        inc_args = tc["function"].get("arguments", "")
                        existing["function"]["arguments"] = (
                            existing["function"].get("arguments", "") + inc_args
                        )

            # 3. 结束标记
            if chunk.finish_reason:
                break

        # 组装最终响应
        full_content = "".join(content_parts)
        final_tool_calls = (
            [tool_calls_acc[i] for i in sorted(tool_calls_acc.keys())] if tool_calls_acc else None
        )
        if stream_started and not (defer_stop_if_tool_calls and final_tool_calls):
            await _emit(
                "message_stop",
                {
                    "agent_id": self.agent.id,
                    "agent_name": self.agent.name,
                    "agent_message_id": stream_message_id,
                    **stream_context,
                },
            )

        return ChatResponse(
            content=full_content,
            tool_calls=final_tool_calls,
            reasoning_content="".join(reasoning_parts),
        )

    async def _build_model_context(
        self,
        *,
        task: str,
        blackboard_view: dict,
        agent_ctx: Optional[AgentContext],
        context_provider: Optional[AgentContextProvider],
        context_metadata: Optional[dict[str, Any]],
    ) -> tuple[str, List[ChatMessage]]:
        system_prompt = self.agent.system_prompt
        metadata = dict(context_metadata or {})
        prompt_task = str(metadata.get("visible_content") or task)
        user_prompt = self._build_prompt(prompt_task, blackboard_view, metadata.get("task_input"))

        if context_provider:
            request = AgentContextBuildRequest(
                session_id=str(metadata.get("conversation_id") or ""),
                agent=self.agent,
                task=task,
                base_system_prompt=system_prompt,
                base_user_prompt=user_prompt,
                blackboard_view=blackboard_view,
                metadata=metadata,
            )
            if not request.session_id:
                request.session_id = str(metadata.get("session_id") or "")
            try:
                result = context_provider.build_agent_context(request)
                if inspect.isawaitable(result):
                    result = await result
                if isinstance(result, AgentContextBuildResult) and result.messages:
                    return self._normalize_context_messages(result, system_prompt)
            except Exception as exc:
                logger.warning(
                    "Agent context provider failed; falling back to runtime context",
                    agent_id=self.agent.id,
                    error=str(exc),
                    exc_info=True,
                )

        messages = self._agent_context_messages(agent_ctx)
        messages.append(
            ChatMessage(
                role="user",
                content=self._build_prompt(task, blackboard_view, metadata.get("task_input")),
            )
        )
        return system_prompt, messages

    def _agent_context_messages(self, agent_ctx: Optional[AgentContext]) -> List[ChatMessage]:
        messages: List[ChatMessage] = []
        if not agent_ctx:
            return messages
        agent_ctx.trim(max_tokens=4000)
        for frame in agent_ctx.frames:
            if frame.frame_type == "thought":
                messages.append(ChatMessage(role="assistant", content=str(frame.content)))
            elif frame.frame_type == "tool_call":
                tc = frame.content
                if isinstance(tc, dict):
                    name = (
                        tc.get("tool_name")
                        or tc.get("function", {}).get("name")
                        or "unknown"
                    )
                    messages.append(ChatMessage(role="assistant", content=f"历史工具调用：{name}"))
            elif frame.frame_type == "tool_result":
                tr = frame.content
                if isinstance(tr, dict):
                    messages.append(
                        ChatMessage(
                            role="assistant",
                            content=f"历史工具结果 {tr.get('tool', 'unknown')}：{tr.get('result', tr.get('error', ''))}",
                        )
                    )
                else:
                    messages.append(ChatMessage(role="assistant", content=f"历史工具结果：{tr}"))
        return messages

    def _normalize_context_messages(
        self,
        result: AgentContextBuildResult,
        fallback_system_prompt: str,
    ) -> tuple[str, List[ChatMessage]]:
        system_parts: list[str] = []
        messages: List[ChatMessage] = []
        if result.system_prompt:
            system_parts.append(str(result.system_prompt))
        for raw in result.messages:
            if not isinstance(raw, dict):
                continue
            role = str(raw.get("role") or "").strip() or "user"
            content = str(raw.get("content") or "")
            if role == "system":
                if content:
                    system_parts.append(content)
                continue
            messages.append(
                ChatMessage(
                    role=role,
                    content=content,
                    name=raw.get("name"),
                    tool_calls=raw.get("tool_calls"),
                    tool_call_id=raw.get("tool_call_id"),
                    reasoning_content=raw.get("reasoning_content"),
                )
            )
        system_prompt = "\n\n".join(part for part in system_parts if part) or fallback_system_prompt
        return system_prompt, messages

    def _build_prompt(
        self,
        task: str,
        blackboard_view: dict,
        task_input: Any | None = None,
    ) -> str:
        """构建用户提示词"""
        # 构建 Blackboard 视图文本
        bb_text = self._format_blackboard(blackboard_view)
        assignment_context = self._format_assignment_context(task_input)

        return f"""你是当前 Agent：{self.agent.name}
角色：{self.agent.role}

请只代表你自己发言，不要冒充其他 Agent。若任务需要协作，只描述你负责的部分和可交付成果。
工具使用规则：
- 用户明确要求生成 PDF、Word、PPT、Excel、HTML、网页、可下载文件、预览卡片或正式产物时，才调用 artifact.create_* 工具。
- 用户只是要求“写一段、介绍、说明、回答、总结文字”，或明确说“直接回复、不需要产物”时，必须直接用聊天文本回答，不要调用 artifact 工具。
- 如果已经调用工具并成功生成产物，仍要给用户一段自然语言回复，说明完成了什么以及如何查看卡片。
- sandbox.run、terminal.start 的 command 不经过 shell，只能是一条可执行命令；不能写 cd、&&、;、|、>、< 或换行。需要进入子目录时使用 workdir 参数，例如 command="python main.py", workdir="backend"。
- 如果运行、测试、部署或终端工具失败，最终回复必须如实说明失败项；不能声称依赖已安装、服务已启动、测试通过或部署成功。

你的当前任务：
{task}

Assignment context (read-only; use upstream_outputs when coordinating with other Agents):
{assignment_context}

全局上下文（只读）：
{bb_text}

请完成以下工作，并在最后输出你的状态报告：

---
工作状态报告（必须包含，格式如下）：
```status_report
{{
  "state": "running|ready|waiting|completed",
  "will": "execute|wait|delegate|complete|blocked",
  "rationale": "简要说明你的当前状态和下一步计划",
  "blockers": [],
  "priority": 1,
  "confidence": 0.95
}}
```
"""

    def _format_assignment_context(self, task_input: Any | None) -> str:
        if not isinstance(task_input, dict) or not task_input:
            return "(none)"
        safe: dict[str, Any] = {}
        for key in ("user_request", "assigned_task", "rationale"):
            if task_input.get(key):
                safe[key] = task_input.get(key)
        plan = task_input.get("plan")
        if isinstance(plan, list):
            safe["plan"] = [
                {
                    "agent_id": item.get("agent_id"),
                    "agent_name": item.get("agent_name"),
                    "role": item.get("role"),
                    "stage": item.get("stage"),
                    "depends_on": item.get("depends_on"),
                    "status": item.get("status"),
                    "task": item.get("task"),
                    "output_preview": item.get("output_preview"),
                }
                for item in plan
                if isinstance(item, dict)
            ]
        upstream = task_input.get("upstream_outputs")
        if isinstance(upstream, dict):
            safe["upstream_outputs"] = {
                agent_id: {
                    "task": value.get("task"),
                    "status": value.get("status"),
                    "output": value.get("output_preview") or value.get("output"),
                    "rationale": value.get("rationale"),
                    "confidence": value.get("confidence"),
                    "blockers": value.get("blockers"),
                }
                for agent_id, value in upstream.items()
                if isinstance(value, dict)
            }
        if not safe:
            return "(none)"
        return json.dumps(safe, ensure_ascii=False, indent=2, default=str)[:12000]

    def _format_blackboard(self, blackboard_view: dict) -> str:
        """格式化 Blackboard 分层视图为文本"""
        parts = []

        # 1. 结构化摘要（替代早期历史）
        summaries = blackboard_view.get("structured_summaries", [])
        if summaries:
            parts.append("历史摘要：")
            for s in summaries[-3:]:
                content = s.get("content", "") if isinstance(s, dict) else str(s)
                parts.append(f"  - {content[:120]}")

        # 2. 近期原始记录（保留细节）
        recent_history = blackboard_view.get("recent_history", [])
        if recent_history:
            parts.append("近期历史：")
            for entry in recent_history[-5:]:
                parts.append(
                    f"  - [{entry.get('type', '?')}] {str(entry.get('content', ''))[:100]}"
                )

        # 3. 结构化键值状态
        kv_state = blackboard_view.get("kv_state", {})
        if kv_state:
            parts.append(f"状态变量：{json.dumps(kv_state, ensure_ascii=False, indent=2)}")

        version = blackboard_view.get("version", 0)
        if version:
            parts.append(f"版本：{version}")

        return "\n".join(parts) if parts else "（无）"

    def _extract_status_report(self, content: str) -> AgentReport:
        """从回复中提取状态报告"""
        return parse_agent_status_report(content, self.agent.id)

        return AgentReport(
            agent_id=self.agent.id,
            state=AgentState.UNKNOWN,
            will=AgentWill.WAIT,
            rationale="无法解析状态报告",
            confidence=0.0,
        )

    def _remove_status_report(self, content: str) -> str:
        """从回复中移除状态报告部分"""

        return _StatusReportStreamFilter._strip_status_report(content)

    def _parse_state(self, state_str: str) -> AgentState:
        """解析状态字符串"""
        state_map = {
            "idle": AgentState.IDLE,
            "ready": AgentState.READY,
            "running": AgentState.RUNNING,
            "paused": AgentState.PAUSED,
            "waiting": AgentState.WAITING,
            "completed": AgentState.COMPLETED,
            "failed": AgentState.FAILED,
        }
        return state_map.get(state_str.lower(), AgentState.UNKNOWN)

    def _parse_will(self, will_str: str) -> AgentWill:
        """解析意图字符串"""
        will_map = {
            "execute": AgentWill.EXECUTE,
            "wait": AgentWill.WAIT,
            "delegate": AgentWill.DELEGATE,
            "complete": AgentWill.COMPLETE,
            "blocked": AgentWill.BLOCKED,
        }

        return will_map.get(will_str.lower(), AgentWill.WAIT)
