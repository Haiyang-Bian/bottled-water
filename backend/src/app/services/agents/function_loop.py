from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import Agent, Conversation, Message, Task, User, WorkflowRun, utcnow
from app.services.agents.function_messages import agent_system_prompt, tool_arguments, tool_names
from app.services.agents.function_types import AgentFunctionLoopResult
from app.services.agents.permission_guard import complete_missing_artifact_tool
from app.services.agents.tool_events import tool_event_from_record
from app.services.agents.tool_loop import build_tools_for_agent, execute_tool_by_name
from app.services.llm.ark import ark_client
from app.services.context.attachments import attachment_preflight_reply
from app.services.context.builder import ContextBuilder
from app.services.context.state import update_conversation_state_after_turn
from app.services.llm.tool_calls import artifact_arguments, detect_artifact_tool
from app.services.llm.html_artifacts import HTML_ARTIFACT_TOOLS, normalize_html_artifact_arguments
from app.services.llm.gateway import stream_model_config_chat
from app.services.output_filter import InternalOutputStreamFilter, strip_internal_agent_output
from app.services.realtime.event_bus import event_bus
from app.services.serialization import message_to_dict, task_to_dict
from app.services.skills.context import activated_skill_context
from app.services.workflows.runtime import _set_workflow_node_state, mutate_workflow_run_locked

logger = logging.getLogger(__name__)


async def _publish_workflow_update(
    db: Session,
    *,
    conversation: Conversation,
    workflow_run: WorkflowRun | None,
    workflow_node_id: str | None,
    channel: str,
    status: str,
    progress: int,
    output: dict[str, Any] | None = None,
    message: str | None = None,
) -> None:
    if not workflow_run or not workflow_node_id:
        return

    def _mutate(run: WorkflowRun) -> None:
        _set_workflow_node_state(
            run,
            workflow_node_id,
            status=status,
            progress=progress,
            output=output,
            message=message,
        )

    await mutate_workflow_run_locked(db, conversation, workflow_run, _mutate)
    await event_bus.publish(
        channel,
        "workflow:run_updated",
        {
            "run_id": workflow_run.id,
            "status": workflow_run.status,
            "progress": workflow_run.progress,
            "node_id": workflow_node_id,
        },
    )


async def _publish_text_delta(
    *,
    channel: str,
    assistant: Message | None,
    agent: Agent,
    text: str,
    delta_type: str = "text_delta",
    emit_message: bool,
) -> None:
    if not emit_message or not assistant or not text:
        return
    await event_bus.publish(
        channel,
        "content_block_delta",
        {
            "agent_message_id": assistant.id,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "agent_avatar_url": agent.avatar_url,
            "delta": {"type": delta_type, "text": text},
        },
    )


async def _publish_message_tool_event(
    *,
    channel: str,
    assistant: Message | None,
    agent: Agent,
    event_name: str,
    tool_name: str,
    tool_call_id: str,
    emit_message: bool,
    status: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    if not emit_message or not assistant:
        return
    await event_bus.publish(
        channel,
        event_name,
        {
            "agent_message_id": assistant.id,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "tool_name": tool_name,
            "tool_call_id": tool_call_id,
            **({"status": status} if status else {}),
            **({"detail": detail} if detail else {}),
        },
    )


async def _fail_agent_loop(
    db: Session,
    *,
    conversation: Conversation,
    assistant: Message | None,
    agent: Agent,
    channel: str,
    workflow_run: WorkflowRun | None,
    workflow_node_id: str | None,
    error_text: str,
    tool_results: list[dict[str, Any]] | None = None,
) -> AgentFunctionLoopResult:
    final_text = strip_internal_agent_output(error_text) or "本轮响应异常结束，已停止生成。"
    if assistant:
        assistant.content = {
            "text": final_text,
            "tool_events": [tool_event_from_record(db, item) for item in (tool_results or [])],
        }
        assistant.status = "failed"
    db.commit()
    if assistant:
        await event_bus.publish(channel, "message:updated", message_to_dict(assistant))
        await event_bus.publish(
            channel,
            "message_stop",
            {
                "agent_message_id": assistant.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "stop_reason": "generation_failed",
            },
        )
    await _publish_workflow_update(
        db,
        conversation=conversation,
        workflow_run=workflow_run,
        workflow_node_id=workflow_node_id,
        channel=channel,
        status="failed",
        progress=100,
        output={"error": final_text, "assistant_message_id": assistant.id if assistant else None},
        message=f"{agent.name} 响应失败",
    )
    return AgentFunctionLoopResult(
        assistant=assistant,
        text=final_text,
        thinking="",
        tool_results=tool_results or [],
        tool_context={
            "mode": "function_call_loop",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "assistant_message_id": assistant.id if assistant else None,
            "status": "failed",
            "error": final_text,
        },
    )


async def _complete_agent_loop_without_model(
    db: Session,
    *,
    conversation: Conversation,
    user_message: Message,
    assistant: Message | None,
    agent: Agent,
    channel: str,
    workflow_run: WorkflowRun | None,
    workflow_node_id: str | None,
    final_text: str,
) -> AgentFunctionLoopResult:
    text = strip_internal_agent_output(final_text)
    if assistant:
        assistant.content = {"text": text, "tool_events": []}
        assistant.status = "completed"
    update_conversation_state_after_turn(
        db,
        conversation,
        user_message=user_message,
        assistant_message=assistant,
        final_text=text,
        tool_results=[],
    )
    db.commit()
    if assistant:
        await _publish_text_delta(
            channel=channel,
            assistant=assistant,
            agent=agent,
            text=text,
            emit_message=True,
        )
        await event_bus.publish(channel, "message:updated", message_to_dict(assistant))
        await event_bus.publish(
            channel,
            "message_stop",
            {
                "agent_message_id": assistant.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "stop_reason": "end_turn",
            },
        )
    await _publish_workflow_update(
        db,
        conversation=conversation,
        workflow_run=workflow_run,
        workflow_node_id=workflow_node_id,
        channel=channel,
        status="completed",
        progress=100,
        output={"summary": text[:1000], "assistant_message_id": assistant.id if assistant else None},
        message=f"{agent.name} 已完成回复",
    )
    return AgentFunctionLoopResult(
        assistant=assistant,
        text=text,
        thinking="",
        tool_results=[],
        tool_context={
            "mode": "function_call_loop",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "assistant_message_id": assistant.id if assistant else None,
            "status": "completed",
            "attachment_preflight": True,
        },
    )


async def run_agent_function_call_loop(
    db: Session,
    *,
    conversation: Conversation,
    user_message: Message,
    agent: Agent,
    prompt: str,
    channel: str,
    mode: str,
    task: Task | None = None,
    workflow_run: WorkflowRun | None = None,
    workflow_node_id: str | None = None,
    node_title: str | None = None,
    node_input: dict[str, Any] | None = None,
    max_tool_rounds: int = 3,
    emit_message: bool = True,
    runtime_model_config_id: str | None = None,
) -> AgentFunctionLoopResult:
    """Run one Agent as an independent OpenAI-style function-call loop."""
    settings = get_settings()
    user = db.get(User, conversation.creator_id)
    tools = build_tools_for_agent(db, agent) if settings.enable_function_calling else []
    allowed_tool_names = tool_names(tools)
    model_config_id = (
        runtime_model_config_id
        or (user_message.extra or {}).get("model_config_id")
        or (agent.config or {}).get("model_config_id")
    )
    logger.info(
        "[agent_function_loop] start agent=%s/%s mode=%s model_config_id=%s exposed_tools=%s",
        agent.name,
        agent.id,
        mode,
        model_config_id,
        sorted(allowed_tool_names),
    )

    assistant: Message | None = None
    if emit_message:
        assistant = Message(
            conversation_id=conversation.id,
            sender_type="agent",
            sender_id=agent.id,
            sender_name=agent.name,
            sender_avatar_url=agent.avatar_url,
            content_type="text",
            content={"text": ""},
            status="streaming",
        )
        db.add(assistant)
        db.commit()
        db.refresh(assistant)
        await event_bus.publish(
            channel,
            "message_start",
            {
                "agent_message_id": assistant.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "agent_avatar_url": agent.avatar_url,
            },
        )

    await _publish_workflow_update(
        db,
        conversation=conversation,
        workflow_run=workflow_run,
        workflow_node_id=workflow_node_id,
        channel=channel,
        status="running",
        progress=20,
        output={
            "agent_id": agent.id,
            "agent_name": agent.name,
            "assistant_message_id": assistant.id if assistant else None,
            "model_config_id": model_config_id,
            "tool_count": len(tools),
            "tool_names": sorted(allowed_tool_names),
        },
        message=f"{agent.name} 正在组织回复",
    )

    requested_artifact_tool = detect_artifact_tool(prompt)
    if (
        requested_artifact_tool == "artifact.create_html"
        and requested_artifact_tool not in allowed_tool_names
        and "artifact.create_web_app" in allowed_tool_names
    ):
        requested_artifact_tool = "artifact.create_web_app"
    if requested_artifact_tool and requested_artifact_tool not in allowed_tool_names:
        logger.info(
            "[agent_function_loop] missing_artifact_tool agent=%s requested=%s exposed_tools=%s",
            agent.id,
            requested_artifact_tool,
            sorted(allowed_tool_names),
        )
        return await complete_missing_artifact_tool(
            db,
            conversation=conversation,
            assistant=assistant,
            agent=agent,
            workflow_run=workflow_run,
            workflow_node_id=workflow_node_id,
            channel=channel,
            requested_tool=requested_artifact_tool,
            publish_workflow_update=_publish_workflow_update,
        )

    preflight_reply = attachment_preflight_reply(
        prompt,
        (user_message.content or {}).get("attachments") or [],
    )
    if preflight_reply:
        return await _complete_agent_loop_without_model(
            db,
            conversation=conversation,
            user_message=user_message,
            assistant=assistant,
            agent=agent,
            channel=channel,
            workflow_run=workflow_run,
            workflow_node_id=workflow_node_id,
            final_text=preflight_reply,
        )

    try:
        skill_context = activated_skill_context(db, agent)
        context_builder = ContextBuilder(db)
        context_bundle = context_builder.build_agent_messages(
            conversation=conversation,
            user_message=user_message,
            agent=agent,
            system_prompt=agent_system_prompt(
                agent,
                mode=mode,
                node_title=node_title,
                skill_context=skill_context,
            ),
            prompt=prompt,
            mode=mode,
            task=task,
            workflow_run=workflow_run,
            workflow_node_id=workflow_node_id,
            node_input=node_input,
        )
    except Exception as exc:
        logger.exception("agent function loop context build failed: agent=%s", agent.id)
        return await _fail_agent_loop(
            db,
            conversation=conversation,
            assistant=assistant,
            agent=agent,
            channel=channel,
            workflow_run=workflow_run,
            workflow_node_id=workflow_node_id,
            error_text=f"{agent.name} 构建上下文失败：{exc}",
        )
    messages: list[dict[str, Any]] = context_bundle.messages
    stream_text = ""
    stream_filter = InternalOutputStreamFilter()
    reasoning_filter = InternalOutputStreamFilter()
    reasoning_text = ""
    tool_results: list[dict[str, Any]] = []
    thinking_enabled = (user_message.extra or {}).get("thinking_enabled") is True
    thinking = {"type": "enabled", "budget_tokens": 1024} if thinking_enabled else None

    for round_num in range(max_tool_rounds + 1):
        current_text = ""
        current_reasoning = ""
        current_tool_calls: list[dict[str, Any]] | None = None
        buffer_text_delta = bool(tools) and round_num == 0
        try:
            if model_config_id:
                event_stream = stream_model_config_chat(
                    db,
                    str(model_config_id),
                    messages,
                    tools=tools if tools else None,
                )
            else:
                event_stream = ark_client.stream_chat(
                    messages,
                    purpose=f"agent:{agent.type}",
                    tools=tools if tools else None,
                    thinking=thinking,
                )
            async for event in event_stream:
                if event.type == "delta":
                    if event.text:
                        current_text += event.text
                        if not buffer_text_delta:
                            stream_text += event.text
                            visible_delta = stream_filter.push(event.text)
                            await _publish_text_delta(
                                channel=channel,
                                assistant=assistant,
                                agent=agent,
                                text=visible_delta,
                                emit_message=emit_message,
                            )
                    if event.reasoning:
                        current_reasoning += event.reasoning
                        reasoning_text += event.reasoning
                        visible_reasoning = reasoning_filter.push(event.reasoning)
                        await _publish_text_delta(
                            channel=channel,
                            assistant=assistant,
                            agent=agent,
                            text=visible_reasoning,
                            delta_type="reasoning_delta",
                            emit_message=emit_message,
                        )
                elif event.type == "tool_calls":
                    current_tool_calls = event.tool_calls or []
                    logger.info(
                        "[agent_function_loop] tool_calls_received agent=%s round=%s calls=%s",
                        agent.id,
                        round_num,
                        [
                            (item.get("function") or {}).get("name")
                            for item in current_tool_calls
                            if isinstance(item, dict)
                        ],
                    )
                elif event.type == "usage" and emit_message and assistant:
                    await event_bus.publish(
                        channel,
                        "usage",
                        {"agent_message_id": assistant.id, "agent_id": agent.id, "usage": event.usage},
                    )
                elif event.type == "error":
                    text = f"\n模型调用异常，已降级：{event.error}"
                    stream_text += text
                    await _publish_text_delta(
                        channel=channel,
                        assistant=assistant,
                        agent=agent,
                        text=text,
                        emit_message=emit_message,
                    )
        except Exception as exc:
            logger.exception("agent function loop stream failed: agent=%s", agent.id)
            if not stream_text:
                stream_text = f"模型调用异常，已降级：{exc}"
                await _publish_text_delta(
                    channel=channel,
                    assistant=assistant,
                    agent=agent,
                    text=stream_text,
                    emit_message=emit_message,
                )
            break

        if (
            round_num == 0
            and requested_artifact_tool
            and requested_artifact_tool in allowed_tool_names
            and not current_tool_calls
        ):
            logger.warning(
                "[agent_function_loop] forcing_artifact_tool agent=%s tool=%s buffered_text=%r",
                agent.id,
                requested_artifact_tool,
                current_text[:120],
            )
            current_tool_calls = [_forced_artifact_tool_call(requested_artifact_tool, prompt)]
            current_text = ""

        if not current_tool_calls:
            if (
                buffer_text_delta
                and current_text
                and (not requested_artifact_tool or not _looks_like_tool_argument_fragment(current_text))
            ):
                stream_text += current_text
                visible_delta = stream_filter.push(current_text)
                await _publish_text_delta(
                    channel=channel,
                    assistant=assistant,
                    agent=agent,
                    text=visible_delta,
                    emit_message=emit_message,
                )

        if not current_tool_calls or round_num >= max_tool_rounds:
            if round_num == 0 and not current_tool_calls:
                logger.info(
                    "[agent_function_loop] no_tool_calls agent=%s model_config_id=%s requested_artifact_tool=%s exposed_tools=%s",
                    agent.id,
                    model_config_id,
                    requested_artifact_tool,
                    sorted(allowed_tool_names),
                )
            break

        round_tool_results: list[dict[str, Any]] = []
        normalized_tool_calls: list[dict[str, Any]] = []
        for raw_tool_call in current_tool_calls:
            tool_call = dict(raw_tool_call) if isinstance(raw_tool_call, dict) else {}
            function = tool_call.get("function") if isinstance(tool_call, dict) else {}
            function = function if isinstance(function, dict) else {}
            tool_name = str(function.get("name") or "")
            tool_call_id = str(tool_call.get("id") or f"call_{round_num}_{len(tool_results) + 1}")
            tool_call["id"] = tool_call_id
            tool_call.setdefault("type", "function")
            tool_call["function"] = function
            normalized_tool_calls.append(tool_call)
            arguments = _tool_arguments_with_context(
                tool_arguments(str(function.get("arguments") or "")),
                conversation=conversation,
                agent=agent,
                task=task,
            )
            arguments = _normalize_tool_arguments_for_request(tool_name, prompt, arguments)
            logger.info(
                "[agent_function_loop] tool_call agent=%s name=%s arguments=%s",
                agent.id,
                tool_name,
                json.dumps(arguments, ensure_ascii=False, default=str)[:1200],
            )
            await _publish_message_tool_event(
                channel=channel,
                assistant=assistant,
                agent=agent,
                event_name="tool_call_start",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                emit_message=emit_message,
                status="running",
            )
            await _publish_workflow_update(
                db,
                conversation=conversation,
                workflow_run=workflow_run,
                workflow_node_id=workflow_node_id,
                channel=channel,
                status="running",
                progress=min(85, 35 + round_num * 15),
                output={
                    "active_tool": tool_name,
                    "tool_calls": [
                        *[
                            {
                                "tool_name": item.get("tool_name"),
                                "tool_call_id": item.get("tool_call_id"),
                                "status": item.get("status"),
                            }
                            for item in tool_results
                        ],
                        {"tool_name": tool_name, "tool_call_id": tool_call_id, "status": "running"},
                    ],
                },
                message=f"{agent.name} 正在调用 {tool_name}",
            )

            if tool_name not in allowed_tool_names:
                result = {
                    "type": "tool",
                    "tool_name": tool_name,
                    "status": "failed",
                    "output": "Agent 未被授权调用该工具",
                }
            else:
                try:
                    result = await execute_tool_by_name(
                        db,
                        agent=agent,
                        user=user,
                        conversation=conversation,
                        tool_name=tool_name,
                        arguments=arguments,
                    )
                except Exception as exc:
                    logger.exception(
                        "[agent_function_loop] tool execution failed agent=%s name=%s",
                        agent.id,
                        tool_name,
                    )
                    result = {
                        "type": "tool",
                        "tool_name": tool_name,
                        "status": "failed",
                        "output": {"error": str(exc)},
                        "error": str(exc),
                    }
            status = str(result.get("status") or "unknown")
            logger.info(
                "[agent_function_loop] tool_result agent=%s name=%s status=%s invocation_id=%s",
                agent.id,
                tool_name,
                status,
                result.get("invocation_id"),
            )
            record = {
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "arguments": arguments,
                "result": result,
                "status": status,
                "round": round_num,
            }
            tool_results.append(record)
            round_tool_results.append(record)
            db.commit()
            tool_event = tool_event_from_record(db, record)
            await _publish_message_tool_event(
                channel=channel,
                assistant=assistant,
                agent=agent,
                event_name="tool_call_done",
                tool_name=tool_name,
                tool_call_id=tool_call_id,
                emit_message=emit_message,
                status=status,
                detail=tool_event,
            )
            await _publish_workflow_update(
                db,
                conversation=conversation,
                workflow_run=workflow_run,
                workflow_node_id=workflow_node_id,
                channel=channel,
                status="running",
                progress=min(90, 45 + round_num * 15),
                output={
                    "tool_results": [
                        {
                            "tool_name": item.get("tool_name"),
                            "tool_call_id": item.get("tool_call_id"),
                            "status": item.get("status"),
                            "result": item.get("result"),
                        }
                        for item in tool_results
                    ]
                },
                message=f"{agent.name} 已完成 {tool_name}",
            )

        assistant_message = {
            "role": "assistant",
            "content": current_text or "",
            "tool_calls": normalized_tool_calls,
        }
        if current_reasoning:
            assistant_message["reasoning_content"] = current_reasoning
        messages.append(assistant_message)
        for item in round_tool_results:
            messages.append(
                context_builder.tool_result_message(
                    conversation=conversation,
                    tool_call_id=item["tool_call_id"],
                    result=item["result"],
                )
            )
        if task:
            task.progress = max(task.progress or 0, min(90, 25 + (round_num + 1) * 15))
            db.commit()
            await event_bus.publish(channel, "task:status_changed", task_to_dict(task))

    if requested_artifact_tool and requested_artifact_tool in allowed_tool_names:
        artifact_success = _artifact_tool_succeeded(tool_results, requested_artifact_tool)
        if not artifact_success:
            return await _fail_agent_loop(
                db,
                conversation=conversation,
                assistant=assistant,
                agent=agent,
                channel=channel,
                workflow_run=workflow_run,
                workflow_node_id=workflow_node_id,
                error_text=(
                    f"{agent.name} 未能生成真实产物：模型没有返回有效工具调用，"
                    "且产物工具未成功返回 artifact_id。"
                ),
                tool_results=tool_results,
            )

    final_text = strip_internal_agent_output(stream_text)
    if requested_artifact_tool and _looks_like_tool_argument_fragment(final_text):
        final_text = ""
    if not final_text:
        final_text = _artifact_success_text(requested_artifact_tool, tool_results) or f"{agent.name} 已完成本次处理。"
    thinking_text = strip_internal_agent_output(reasoning_text) if reasoning_text else ""
    assistant_message_id = assistant.id if assistant else None
    tool_events = [tool_event_from_record(db, item) for item in tool_results]
    if assistant:
        assistant.content = {"text": final_text, "thinking": thinking_text, "tool_events": tool_events}
        assistant.status = "completed"
    update_conversation_state_after_turn(
        db,
        conversation,
        user_message=user_message,
        assistant_message=assistant,
        final_text=final_text,
        tool_results=tool_results,
    )
    db.commit()
    if assistant:
        await event_bus.publish(channel, "message:updated", message_to_dict(assistant))
        await event_bus.publish(
            channel,
            "message_stop",
            {
                "agent_message_id": assistant.id,
                "agent_id": agent.id,
                "agent_name": agent.name,
                "stop_reason": "end_turn",
            },
        )

    await _publish_workflow_update(
        db,
        conversation=conversation,
        workflow_run=workflow_run,
        workflow_node_id=workflow_node_id,
        channel=channel,
        status="completed",
        progress=100,
        output={
            "summary": final_text[:1000],
            "tool_results": tool_results,
            "assistant_message_id": assistant_message_id,
            "completed_at": utcnow().isoformat(),
        },
        message=f"{agent.name} 已完成回复",
    )
    return AgentFunctionLoopResult(
        assistant=assistant,
        text=final_text,
        thinking=thinking_text,
        tool_results=tool_results,
        tool_context={
            "mode": "function_call_loop",
            "agent_id": agent.id,
            "agent_name": agent.name,
            "assistant_message_id": assistant_message_id,
            "model_config_id": model_config_id,
            "executions": [item["result"] for item in tool_results],
            "tool_results": tool_results,
            "context": context_bundle.diagnostics,
            "summary": "\n".join(
                f"- {item.get('tool_name')}: {item.get('status')}" for item in tool_results
            ),
        },
    )


def _forced_artifact_tool_call(tool_name: str, prompt: str) -> dict[str, Any]:
    return {
        "id": f"call_forced_{tool_name.replace('.', '_')}",
        "type": "function",
        "function": {
            "name": tool_name,
            "arguments": json.dumps(artifact_arguments(tool_name, prompt), ensure_ascii=False),
        },
    }


def _normalize_tool_arguments_for_request(
    tool_name: str,
    prompt: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if tool_name in HTML_ARTIFACT_TOOLS:
        return normalize_html_artifact_arguments(prompt, arguments)
    return arguments


def _looks_like_tool_argument_fragment(text: str) -> bool:
    normalized = (text or "").strip()
    if not normalized:
        return False
    lower = normalized.lower()
    if lower in {"0", "1", ">", "<", "/>", "li", "/li", "<li", "</li>", "ul", "/ul"}:
        return True
    if len(lower) <= 3 and all(ch.isalnum() or ch in "<>/{}[],:;\"'" for ch in lower):
        return True
    return False


def _artifact_tool_succeeded(tool_results: list[dict[str, Any]], requested_tool: str) -> bool:
    for item in tool_results:
        if item.get("tool_name") != requested_tool or item.get("status") != "succeeded":
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        output = result.get("output") if isinstance(result, dict) else None
        if isinstance(output, dict) and output.get("artifact_id"):
            return True
    return False


def _artifact_success_text(requested_tool: str | None, tool_results: list[dict[str, Any]]) -> str:
    if not requested_tool:
        return ""
    for item in tool_results:
        if item.get("tool_name") != requested_tool or item.get("status") != "succeeded":
            continue
        result = item.get("result") if isinstance(item.get("result"), dict) else {}
        output = result.get("output") if isinstance(result, dict) else {}
        if isinstance(output, dict) and output.get("artifact_id"):
            return _natural_artifact_success_text(requested_tool, output)
    return ""


def _natural_artifact_success_text(requested_tool: str, output: dict[str, Any]) -> str:
    label = {
        "artifact.create_pdf": "PDF",
        "artifact.create_docx": "Word",
        "artifact.create_pptx": "PPT",
        "artifact.create_xlsx": "Excel",
        "artifact.create_html": "HTML",
        "artifact.create_web_app": "HTML",
    }.get(requested_tool, str(output.get("format") or "产物").upper())
    title = _artifact_display_title(output)
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


def _artifact_display_title(output: dict[str, Any]) -> str:
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
    return re.sub(r"\s+", " ", title)[:80]


def _tool_arguments_with_context(
    arguments: dict[str, Any],
    *,
    conversation: Conversation,
    agent: Agent,
    task: Task | None,
) -> dict[str, Any]:
    extra = conversation.extra if isinstance(conversation.extra, dict) else {}
    enriched = dict(arguments)
    enriched.setdefault("conversation_id", conversation.id)
    enriched.setdefault("agent_id", agent.id)
    if task:
        enriched.setdefault("task_id", task.id)
    workspace_id = extra.get("workspace_id") or extra.get("workspaceId")
    if workspace_id:
        enriched.setdefault("workspace_id", str(workspace_id))
    return enriched
