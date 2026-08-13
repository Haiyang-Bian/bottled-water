"""
Conversations API

会话 CRUD、成员管理和工作流编排，统一使用 model_provider。
"""

from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import re
from typing import Any

from fastapi import APIRouter, Body, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import SessionLocal
from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.core.response import ok
from app.deps import get_current_user
from db import get_db
from db.models import (
    Agent,
    Conversation,
    ConversationParticipant,
    Message,
    Task,
    User,
    Workspace,
    WorkspaceMember,
    WorkflowRun,
    utcnow,
)
from app.schemas.common import ApiResponse
from app.schemas.requests import (
    AddParticipantRequest,
    CreateConversationRequest,
    InviteParticipantRequest,
    ParticipantRoleUpdatePayload,
    UpdateConversationRequest,
    WorkflowGeneratePayload,
    WorkflowUpdatePayload,
)
from app.services.chat.scheduling import normalize_scheduling_strategy
from app.services.conversation_identity import generate_conversation_number
from app.services.realtime.event_bus import event_bus
from app.services.serialization import (
    conversation_to_dict,
    participant_to_dict,
    task_to_dict,
    workflow_run_to_dict,
)
from app.services.tasks.service import create_task_for_prompt
from app.services.model_config_resolver import create_provider_from_db
from app.services.workflows.definition import _conversation_agents
from app.services.workflows.engine import WorkflowEngine
from app.services.workflows.runtime import (
    _set_workflow_node_state,
    _sync_workflow_run,
    append_run_event,
    build_edge_states,
    build_node_states,
    mark_json_field_modified,
)
from app.services.workflows.validator import format_workflow_validation_message, validate_workflow_graph
from model_provider.core.interfaces import ChatMessage

router = APIRouter(tags=["conversations"])
compat_router = APIRouter(tags=["conversations-compat"])

WORKFLOW_NODE_TYPES = {
    "start",
    "agent",
    "tool",
    "skill",
    "mcp",
    "condition",
    "loop",
    "review",
    "artifact",
    "end",
}


async def _model_provider(db: AsyncSession):
    return await create_provider_from_db(db)


def _conversation_query(user_id: str):
    return (
        select(Conversation)
        .options(
            selectinload(Conversation.participants).selectinload(ConversationParticipant.agent)
        )
        .where(
            or_(
                Conversation.creator_id == user_id,
                Conversation.participants.any(
                    and_(
                        ConversationParticipant.user_id == user_id,
                        ConversationParticipant.left_at.is_(None),
                    ),
                ),
            ),
            Conversation.deleted_at.is_(None),
        )
    )


async def _accessible_workspace(
    db: AsyncSession, user: User, workspace_id: str | None
) -> Workspace | None:
    if not workspace_id:
        return None
    member_ids = select(WorkspaceMember.workspace_id).where(
        WorkspaceMember.user_id == user.id, WorkspaceMember.left_at.is_(None)
    )
    workspace = await db.scalar(
        select(Workspace).where(
            Workspace.id == workspace_id,
            Workspace.deleted_at.is_(None),
            (Workspace.owner_id == user.id) | (Workspace.id.in_(member_ids)),
        )
    )
    if not workspace:
        raise NotFoundError("Workspace not found")
    return workspace


def _conversation_workspace_id(conversation: Conversation) -> str | None:
    extra = conversation.extra if isinstance(conversation.extra, dict) else {}
    value = extra.get("workspace_id")
    return value if isinstance(value, str) and value else None


async def _list(
    db: AsyncSession,
    user: User,
    workspace_id: str | None = None,
    *,
    include_workspace: bool = False,
) -> list[dict]:
    await _accessible_workspace(db, user, workspace_id)
    conversations = (
        await db.scalars(
            _conversation_query(user.id).order_by(
                Conversation.is_pinned.desc(),
                Conversation.last_message_at.desc().nullslast(),
                Conversation.updated_at.desc(),
            )
        )
    ).all()
    if workspace_id:
        conversations = [
            it for it in conversations if _conversation_workspace_id(it) == workspace_id
        ]
    elif not include_workspace:
        conversations = [it for it in conversations if _conversation_workspace_id(it) is None]
    return [conversation_to_dict(it) for it in conversations]


async def _create(db: AsyncSession, user: User, payload: dict) -> Conversation:
    workspace_id = payload.get("workspace_id")
    if workspace_id:
        await _accessible_workspace(db, user, str(workspace_id))
    chat_type = (
        payload.get("type")
        if payload.get("type") is not None
        else payload.get("chat_type") or ("group" if payload.get("group") else "single")
    )
    agents = (await db.scalars(select(Agent).where(Agent.deleted_at.is_(None)))).all()
    requested = payload.get("participant_agent_ids") or payload.get("agent_ids") or []
    if requested:
        selected = [a for a in agents if a.id in requested]
    else:
        selected = [a for a in agents if a.type in {"master", "frontend", "backend", "reviewer"}]
    if not (1 <= len(selected) <= 8):
        raise ValidationAppError("会话参与者须为1-8个Agent")
    title = payload.get("title") or (
        "新的多 Agent 协作群" if len(selected) > 1 else f"{selected[0].name} · 单聊"
    )
    is_group = chat_type == "group" or len(selected) > 1
    workflow_enabled = (
        bool(payload.get("workflow_enabled"))
        if payload.get("workflow_enabled") is not None
        else False
    )
    requested_strategy = normalize_scheduling_strategy(payload.get("scheduling_strategy"))
    scheduling_strategy = (
        "single_agent"
        if not is_group
        else ("workflow" if workflow_enabled and requested_strategy == "workflow" else "tech_lead")
    )
    conversation_number = generate_conversation_number()
    conversation = Conversation(
        creator_id=user.id,
        chat_type=chat_type,
        title=title,
        description=payload.get("description") or "",
        extra={
            "workspace_id": str(workspace_id) if workspace_id else None,
            "conversation_number": conversation_number,
            "group_number": conversation_number if is_group else None,
            "master_enabled": bool(payload.get("master_enabled", len(selected) > 1)),
            "category": payload.get("category") or "Default",
            "folder": payload.get("folder") or "Default",
            "remark": payload.get("remark") or "",
            "scheduling_strategy": scheduling_strategy,
            "workflow_enabled": workflow_enabled and scheduling_strategy == "workflow",
        },
        last_message_preview="",
        last_message_sender="",
        last_message_at=None,
        activity_score=50,
        message_count=0,
    )
    db.add(conversation)
    await db.flush()
    for agent in selected:
        db.add(
            ConversationParticipant(
                conversation_id=conversation.id,
                participant_type="agent",
                agent_id=agent.id,
                role="member",
            )
        )
    db.add(
        ConversationParticipant(
            conversation_id=conversation.id,
            participant_type="user",
            user_id=user.id,
            role="owner",
        )
    )
    await db.commit()
    return (
        await db.scalars(_conversation_query(user.id).where(Conversation.id == conversation.id))
    ).one()


async def _get(db: AsyncSession, user: User, conversation_id: str) -> Conversation:
    conv = await db.scalar(
        _conversation_query(user.id)
        .where(Conversation.id == conversation_id)
        .execution_options(populate_existing=True)
    )
    if not conv:
        raise NotFoundError("会话不存在")
    return conv


def _active_participants(conversation: Conversation) -> list[ConversationParticipant]:
    return [it for it in conversation.participants if it.left_at is None]


def _current_role(conversation: Conversation, user: User) -> str:
    if conversation.creator_id == user.id:
        return "owner"
    for participant in conversation.participants:
        if participant.user_id == user.id and participant.left_at is None:
            return participant.role
    return "member"


def _ensure_can_manage(conversation: Conversation, user: User) -> None:
    if _current_role(conversation, user) not in {"owner", "admin"} and user.role != "admin":
        raise ForbiddenError("只有所有者或管理员可以管理会话成员")


def _parse_json_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else None
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError:
        return None


def _is_mock_model_provider(provider: Any) -> bool:
    return (
        provider is None
        or provider.__class__.__name__.lower().startswith("_mock")
        or str(getattr(provider, "model", "")).lower() == "mock"
    )


def _looks_like_workflow(value: Any) -> bool:
    return isinstance(value, dict) and isinstance(value.get("nodes"), list)


def _extract_workflow_object(value: Any) -> dict | None:
    if _looks_like_workflow(value):
        return value
    if isinstance(value, dict) and _looks_like_workflow(value.get("workflow")):
        return value["workflow"]
    return None


def _agent_text(agent: Agent) -> str:
    values = [
        agent.id,
        agent.name,
        agent.type,
        agent.description,
    ]
    extra = agent.extra if isinstance(agent.extra, dict) else {}
    values.extend(str(extra.get(key) or "") for key in ("display_name", "provider"))
    for item in agent.capabilities or []:
        if isinstance(item, dict):
            values.extend(str(item.get(key) or "") for key in ("label", "name", "category"))
        elif item:
            values.append(str(item))
    return " ".join(value for value in values if value).lower()


def _node_text(node: dict, config: dict) -> str:
    values = [
        node.get("id"),
        node.get("title"),
        node.get("name"),
        node.get("role"),
        node.get("type"),
        node.get("meta"),
        node.get("description"),
        config.get("agent_name"),
        config.get("agent_type"),
        config.get("role"),
    ]
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    values.extend(data.get(key) for key in ("title", "label", "agent_name", "agent_type"))
    return " ".join(str(value) for value in values if value).lower()


def _agent_hint_values(node: dict, config: dict) -> list[str]:
    values: list[Any] = [
        node.get("agent_id"),
        node.get("agentId"),
        node.get("assigned_agent_id"),
        node.get("agent"),
        config.get("agent_id"),
        config.get("agentId"),
        config.get("assigned_agent_id"),
    ]
    for container in (node.get("data"), config.get("agent")):
        if isinstance(container, dict):
            values.extend(container.get(key) for key in ("id", "agent_id", "agentId", "name"))
    return [str(value).strip() for value in values if str(value or "").strip()]


def _resolve_workflow_agent_id(
    node_type: str,
    node: dict,
    config: dict,
    agents: list[Agent],
    used_agent_ids: set[str],
) -> str | None:
    if node_type not in {"agent", "review"}:
        return None
    if not agents:
        return None

    by_id = {agent.id: agent for agent in agents}
    hints = _agent_hint_values(node, config)
    for hint in hints:
        if hint in by_id:
            return hint

    lowered_hints = [hint.lower() for hint in hints]
    for agent in agents:
        agent_label = _agent_text(agent)
        if any(hint and (hint in agent_label or agent.id.lower().startswith(hint)) for hint in lowered_hints):
            return agent.id

    text = _node_text(node, config)
    scored: list[tuple[int, int, Agent]] = []
    for index, agent in enumerate(agents):
        score = 0
        agent_label = _agent_text(agent)
        if node_type == "review" and agent.type == "reviewer":
            score += 8
        if node_type == "agent" and agent.type != "reviewer":
            score += 2
        if agent.type and agent.type.lower() in text:
            score += 6
        if agent.name and agent.name.lower() in text:
            score += 10
        for token in text.split():
            if len(token) >= 3 and token in agent_label:
                score += 1
        if agent.id in used_agent_ids:
            score -= 3
        scored.append((score, -index, agent))

    scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
    if scored and scored[0][0] > 0:
        return scored[0][2].id

    preferred = [
        agent
        for agent in agents
        if agent.id not in used_agent_ids
        and ((node_type == "review" and agent.type == "reviewer") or node_type == "agent")
    ]
    if not preferred:
        preferred = [agent for agent in agents if agent.id not in used_agent_ids]
    return (preferred or agents)[0].id


def _fallback_edges_for_nodes(nodes: list[dict[str, Any]]) -> list[list[str]]:
    if len(nodes) < 2:
        return []
    start = next((node for node in nodes if node.get("type") == "start"), None)
    end = next((node for node in reversed(nodes) if node.get("type") == "end"), None)
    executable = [
        node
        for node in nodes
        if node.get("type") not in {"start", "end"}
    ]
    if start and end and executable:
        return [
            *[[str(start["id"]), str(node["id"])] for node in executable],
            *[[str(node["id"]), str(end["id"])] for node in executable],
        ]
    return [[str(nodes[index]["id"]), str(nodes[index + 1]["id"])] for index in range(len(nodes) - 1)]


def _workflow_node_type(node: dict, role: str) -> str:
    node_id = str(node.get("id") or "").lower().strip()
    title = str(node.get("title") or node.get("name") or "").lower().strip()
    normalized = role.lower().strip()
    if node_id == "start" or title == "start" or normalized in {"input", "start"}:
        return "start"
    if node_id == "end" or title == "end" or normalized == "end":
        return "end"
    raw_type = str(node.get("type") or "").lower().strip()
    if raw_type in WORKFLOW_NODE_TYPES:
        return raw_type
    if normalized in {"review", "reviewer"}:
        return "review"
    if normalized in {"artifact", "deploy", "delivery", "publish"}:
        return "artifact"
    return "agent"


def _node_config_defaults(node_type: str, node: dict) -> dict:
    raw_config = node.get("config") if isinstance(node.get("config"), dict) else {}
    config = dict(raw_config)
    for key in ("agent_id", "agentId", "assigned_agent_id"):
        if node.get(key):
            config.setdefault("agent_id", node.get(key))
    if isinstance(node.get("agent"), dict):
        agent = node["agent"]
        config.setdefault("agent_id", agent.get("id") or agent.get("agent_id"))
        config.setdefault("agent_name", agent.get("name"))
    if node_type == "tool":
        config.setdefault("tool_name", node.get("tool_name") or node.get("name") or "")
    elif node_type == "skill":
        config.setdefault("skill_id", node.get("skill_id") or "")
    elif node_type == "mcp":
        config.setdefault("server_id", node.get("server_id") or "")
        config.setdefault("tool_name", node.get("tool_name") or "")
    elif node_type == "condition":
        config.setdefault("expression", node.get("expression") or "true")
        config.setdefault(
            "branches",
            node.get("branches") if isinstance(node.get("branches"), list) else ["true", "false"],
        )
    elif node_type == "loop":
        try:
            max_iterations = int(config.get("max_iterations") or node.get("max_iterations") or 3)
        except (TypeError, ValueError):
            max_iterations = 3
        config["max_iterations"] = max(1, min(max_iterations, 20))
    elif node_type == "artifact":
        config.setdefault("artifact_type", node.get("artifact_type") or "html")
    return config


def _fallback_workflow(conversation: Conversation) -> dict:
    agents = [it.agent for it in _active_participants(conversation) if it.agent]
    start_node = {
        "id": "start",
        "title": "Start",
        "type": "start",
        "role": "start",
        "status": "ready",
        "meta": "用户输入与上下文入口",
        "config": {"input": "message"},
    }
    end_node = {
        "id": "end",
        "title": "End",
        "type": "end",
        "role": "end",
        "status": "ready",
        "meta": "汇总最终回复",
        "config": {"output": "assistant_message"},
    }
    agent_nodes = [
        {
            "id": f"agent-{agent.id[:8]}",
            "title": (
                agent.extra.get("display_name")
                if isinstance(agent.extra, dict) and agent.extra.get("display_name")
                else agent.name
            ),
            "type": "review" if agent.type == "reviewer" else "agent",
            "role": agent.type or "agent",
            "status": agent.status,
            "meta": agent.description[:60] or agent.type,
            "agent_id": agent.id,
            "config": {
                "agent_id": agent.id,
                "tools": (agent.config or {}).get("tools", []),
                "skill_ids": (agent.config or {}).get("skill_ids", []),
                "mcp_server_ids": (agent.config or {}).get("mcp_server_ids", []),
            },
        }
        for agent in agents[:8]
    ]
    edges = [["start", node["id"]] for node in agent_nodes]
    edges.extend([[node["id"], "end"] for node in agent_nodes])
    return {
        "conversation_id": conversation.id,
        "mode": "all_agents_independent",
        "nodes": [start_node, *agent_nodes, end_node],
        "edges": edges or [["start", "end"]],
        "settings": {
            "default_policy": "all active agents reply independently",
            "review_policy": "optional",
        },
    }


def _normalize_workflow(value: dict, conversation: Conversation) -> dict:
    fallback = _fallback_workflow(conversation)
    agents = [it.agent for it in _active_participants(conversation) if it.agent]
    active_agent_ids = {agent.id for agent in agents}
    nodes = value.get("nodes") if isinstance(value.get("nodes"), list) else fallback["nodes"]
    edges = value.get("edges") if isinstance(value.get("edges"), list) else fallback["edges"]
    normalized_nodes = []
    seen_node_ids: set[str] = set()
    used_agent_ids: set[str] = set()
    for index, node in enumerate(nodes[:40]):
        if not isinstance(node, dict):
            continue
        raw_role = str(node.get("role") or node.get("type") or "agent")
        role = raw_role
        node_type = _workflow_node_type(node, role)
        if node_type in {"start", "end"} and raw_role == "agent":
            role = node_type
        raw_meta = node.get("meta") or node.get("description") or ""
        if isinstance(raw_meta, dict):
            raw_meta = ", ".join(str(it) for it in raw_meta.get("capabilities", [])[:3]) or ""
        elif isinstance(raw_meta, list):
            raw_meta = ", ".join(str(it) for it in raw_meta[:4])
        config = _node_config_defaults(node_type, node)
        agent_id = node.get("agent_id") or config.get("agent_id")
        if node_type in {"agent", "review"}:
            if not agent_id or str(agent_id) not in active_agent_ids:
                agent_id = _resolve_workflow_agent_id(
                    node_type,
                    node,
                    config,
                    agents,
                    used_agent_ids,
                )
            if not agent_id or str(agent_id) not in active_agent_ids:
                continue
            agent_id = str(agent_id)
            config["agent_id"] = agent_id
            used_agent_ids.add(agent_id)
        node_id = str(node.get("id") or f"node-{index + 1}").strip() or f"node-{index + 1}"
        if node_id in seen_node_ids:
            node_id = f"{node_id}-{index + 1}"
        seen_node_ids.add(node_id)
        title = str(
            node.get("title")
            or node.get("name")
            or config.get("agent_name")
            or f"节点 {index + 1}"
        )[:80]
        if title.lower() == "undefined":
            title = f"节点 {index + 1}"
        normalized_nodes.append(
            {
                "id": node_id,
                "title": title,
                "type": node_type,
                "role": role,
                "status": str(node.get("status") or "ready"),
                "meta": str(raw_meta)[:160],
                "config": config,
                **({"position": node.get("position")} if isinstance(node.get("position"), dict) else {}),
                **({"data": node.get("data")} if isinstance(node.get("data"), dict) else {}),
                **({"agent_id": agent_id} if agent_id else {}),
            }
        )
    node_ids = {node["id"] for node in normalized_nodes}
    normalized_edges = []
    for edge in edges[:80]:
        source = target = None
        if isinstance(edge, list) and len(edge) == 2:
            source, target = edge[0], edge[1]
        elif isinstance(edge, dict):
            source = edge.get("source") or edge.get("from")
            target = edge.get("target") or edge.get("to")
        if source is None or target is None:
            continue
        if str(source) not in node_ids or str(target) not in node_ids:
            continue
        if isinstance(edge, dict):
            normalized_edges.append(
                {
                    **edge,
                    "source": str(source),
                    "target": str(target),
                    "from": str(source),
                    "to": str(target),
                }
            )
        else:
            normalized_edges.append([str(source), str(target)])
    return {
        **fallback,
        **{k: value[k] for k in ("mode", "output_mode", "settings") if k in value},
        "nodes": normalized_nodes or fallback["nodes"],
        "edges": normalized_edges or _fallback_edges_for_nodes(normalized_nodes) or fallback["edges"],
    }


def _new_node_states(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    states = build_node_states(workflow)
    if states:
        states[0]["status"] = "running"
        states[0]["progress"] = 5
        states[0]["started_at"] = utcnow().isoformat()
    return states


def _sync_workflow_runtime(conversation: Conversation, run: WorkflowRun) -> None:
    conversation.extra = {
        **(conversation.extra or {}),
        "workflow_runtime": {
            "run_id": run.id,
            "status": run.status,
            "progress": run.progress,
            "node_states": run.node_states or [],
            "updated_at": utcnow().isoformat(),
        },
    }


def _manual_workflow_prompt(workflow: dict[str, Any]) -> str:
    nodes = workflow.get("nodes") if isinstance(workflow.get("nodes"), list) else []
    start_node = next(
        (
            node
            for node in nodes
            if isinstance(node, dict) and str(node.get("type") or node.get("role") or "").lower() == "start"
        ),
        None,
    )
    if not isinstance(start_node, dict):
        return "请按当前工作流执行。"
    config = start_node.get("config") if isinstance(start_node.get("config"), dict) else {}
    for key in ("manual_input", "prompt", "message", "text"):
        value = config.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "请按当前工作流执行。"


async def _publish_manual_task_started(task: Task, channel: str) -> None:
    await event_bus.publish(channel, "task:status_changed", task_to_dict(task))


def _mark_manual_workflow_run_failed(workflow_run: WorkflowRun, error: str) -> None:
    states = deepcopy(workflow_run.node_states or [])
    running_index = next(
        (
            index
            for index, state in enumerate(states)
            if state.get("status") in {"running", "reviewing"}
        ),
        None,
    )
    if running_index is None:
        running_index = next(
            (
                index
                for index, state in enumerate(states)
                if state.get("status") in {"queued", "ready", None}
            ),
            None,
        )
    failed_node_id = str(states[running_index]["id"]) if running_index is not None else None
    if failed_node_id:
        _set_workflow_node_state(
            workflow_run,
            failed_node_id,
            status="failed",
            progress=100,
            output={"error": error, "source": "manual_workflow_run"},
            error=error,
            message="Workflow run failed before this node completed",
        )
        states = deepcopy(workflow_run.node_states or [])
    for index, state in enumerate(states):
        if index == running_index or state.get("status") not in {"queued", "ready"}:
            continue
        state["status"] = "skipped"
        state["progress"] = 100
        state["message"] = "Skipped because workflow run failed"
        state["completed_at"] = utcnow().isoformat()
        state["output"] = {
            **(state.get("output") or {}),
            "reason": "workflow_failed",
            **({"failed_dependency": failed_node_id} if failed_node_id else {}),
        }
    workflow_run.node_states = states
    mark_json_field_modified(workflow_run, "node_states")
    workflow_run.status = "failed"
    workflow_run.progress = max(
        workflow_run.progress or 0,
        int(
            len(
                [
                    state
                    for state in states
                    if state.get("status")
                    in {"completed", "succeeded", "skipped", "failed", "error"}
                ]
            )
            / max(1, len(states))
            * 100
        ),
    )
    workflow_run.completed_at = utcnow()
    append_run_event(
        workflow_run,
        "run.failed",
        {"error": error, "source": "manual_workflow_run", "node_id": failed_node_id},
    )


async def _execute_manual_workflow_run(
    *,
    conversation_id: str,
    run_id: str,
    prompt: str,
) -> None:
    db = SessionLocal()
    try:
        conversation = db.get(Conversation, conversation_id)
        workflow_run = db.get(WorkflowRun, run_id)
        if not conversation or not workflow_run:
            return
        workflow = workflow_run.workflow_snapshot if isinstance(workflow_run.workflow_snapshot, dict) else {}
        channel = f"conversation:{conversation.id}"
        task = create_task_for_prompt(db, conversation, prompt)
        task.status = "EXECUTING"
        task.started_at = utcnow()
        task.progress = 20
        db.commit()
        await _publish_manual_task_started(task, channel)

        transient_message = Message(
            conversation_id=conversation.id,
            sender_type="system",
            sender_name="Workflow Canvas",
            content_type="text",
            content={"text": prompt, "attachments": []},
            status="completed",
            extra={"thinking_enabled": False},
        )
        agents = _conversation_agents(db, conversation)
        engine = WorkflowEngine(
            db,
            conversation=conversation,
            user_message=transient_message,
            task=task,
            workflow_run=workflow_run,
            workflow=workflow,
            prompt=prompt,
            channel=channel,
            agents=agents,
        )
        await engine.run()
        db.refresh(workflow_run)
        if workflow_run.status == "failed":
            task.status = "FAILED"
            task.completed_at = utcnow()
            task.progress = max(task.progress or 0, workflow_run.progress or 0)
            task.error_info = {
                "workflow_run_id": workflow_run.id,
                "events": (workflow_run.events or [])[-10:],
            }
        elif workflow_run.status == "cancelled":
            task.status = "CANCELLED"
            task.completed_at = utcnow()
            task.progress = min(max(task.progress or 0, workflow_run.progress or 0), 95)
        else:
            task.status = "COMPLETED"
            task.completed_at = utcnow()
            task.progress = 100
            task.output = {
                "workflow_run_id": workflow_run.id,
                "status": workflow_run.status,
                "node_states": workflow_run.node_states or [],
                "events": (workflow_run.events or [])[-20:],
            }
        db.commit()
        await event_bus.publish(channel, "task:status_changed", task_to_dict(task))
    except Exception as exc:
        conversation = db.get(Conversation, conversation_id)
        workflow_run = db.get(WorkflowRun, run_id)
        if conversation and workflow_run:
            _mark_manual_workflow_run_failed(workflow_run, str(exc))
            _sync_workflow_run(conversation, workflow_run)
            db.commit()
            await event_bus.publish(
                f"conversation:{conversation.id}",
                "workflow:run_updated",
                workflow_run_to_dict(workflow_run),
            )
    finally:
        db.close()


async def _patch(db: AsyncSession, user: User, conversation_id: str, payload: dict) -> Conversation:
    conversation = await _get(db, user, conversation_id)
    action = payload.get("action")
    if not action:
        if payload.get("pinned") is not None:
            action = "pin" if payload["pinned"] else "unpin"
        elif payload.get("archived") is not None:
            action = "archive" if payload["archived"] else "unarchive"
        elif any(payload.get(k) is not None for k in ("scheduling_strategy", "workflow_enabled")):
            action = "runtime"
        elif any(payload.get(k) is not None for k in ("title", "description", "remark", "category", "folder")):
            action = "rename"
    if action == "pin":
        conversation.is_pinned = True
        conversation.pinned_at = utcnow()
    elif action == "unpin":
        conversation.is_pinned = False
        conversation.pinned_at = None
    elif action == "archive":
        conversation.status = "archived"
    elif action == "unarchive":
        conversation.status = "active"
    elif action == "rename":
        title = payload.get("title")
        if title is None and any(
            payload.get(k) is not None for k in ("description", "remark", "category", "folder")
        ):
            title = conversation.title
        if not title:
            raise ValidationAppError("标题不能为空")
        conversation.title = str(title).strip()[:200]
        if payload.get("description") is not None:
            conversation.description = str(payload.get("description") or "")[:1000]
        extra = dict(conversation.extra or {})
        for key in ("remark", "category", "folder"):
            if payload.get(key) is not None:
                value = str(payload.get(key) or "").strip()
                extra[key] = (
                    value[:120] if value else ("Default" if key in {"category", "folder"} else "")
                )
        conversation.extra = extra
    elif action == "runtime":
        extra = dict(conversation.extra or {})
        requested_strategy = normalize_scheduling_strategy(payload.get("scheduling_strategy"))
        if conversation.chat_type == "single":
            strategy = "single_agent"
        elif requested_strategy == "workflow" and bool(payload.get("workflow_enabled")):
            strategy = "workflow"
        else:
            strategy = "tech_lead"
        extra["scheduling_strategy"] = strategy
        extra["workflow_enabled"] = bool(payload.get("workflow_enabled")) and strategy == "workflow"
        conversation.extra = extra
    else:
        raise ValidationAppError("不支持的操作类型")
    await db.commit()
    return await _get(db, user, conversation.id)


@router.get("/conversations", response_model=ApiResponse[dict])
async def list_conversations(
    workspace_id: str | None = None,
    include_workspace: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    items = await _list(db, user, workspace_id, include_workspace=include_workspace)
    return ok(
        {
            "items": items,
            "pinned": [it for it in items if it["is_pinned"]],
            "active": [it for it in items if it["status"] == "active" and not it["is_pinned"]],
            "archived": [it for it in items if it["status"] == "archived"],
            "counts": {"total": len(items)},
        }
    )


@router.post("/conversations", response_model=ApiResponse[dict])
async def create_conversation(
    payload: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok(conversation_to_dict(await _create(db, user, payload.model_dump())), "会话创建成功")


@router.get("/conversations/{conversation_id}", response_model=ApiResponse[dict])
async def get_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return ok(conversation_to_dict(await _get(db, user, conversation_id)))


@router.get("/conversations/{conversation_id}/workflow", response_model=ApiResponse[dict])
async def get_conversation_workflow(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    conversation = await _get(db, user, conversation_id)
    workflow = (conversation.extra or {}).get("workflow")
    if not isinstance(workflow, dict):
        workflow = _fallback_workflow(conversation)
    return ok(_normalize_workflow(workflow, conversation))


@router.patch("/conversations/{conversation_id}/workflow", response_model=ApiResponse[dict])
async def update_conversation_workflow(
    conversation_id: str,
    payload: WorkflowUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    workflow = _normalize_workflow(payload.model_dump(), conversation)
    workflow["settings"] = {**(workflow.get("settings") or {}), "edited_by_user": True}
    enabled = bool((workflow.get("settings") or {}).get("enabled"))
    if conversation.chat_type == "single":
        strategy = "single_agent"
    elif enabled:
        strategy = "workflow"
    else:
        strategy = "tech_lead"
    conversation.extra = {
        **(conversation.extra or {}),
        "workflow": workflow,
        "workflow_enabled": enabled and strategy == "workflow",
        "scheduling_strategy": strategy,
    }
    await db.commit()
    return ok(workflow, "工作流已保存")


@router.post("/conversations/{conversation_id}/workflow/generate", response_model=ApiResponse[dict])
async def generate_conversation_workflow(
    conversation_id: str,
    payload: WorkflowGeneratePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    fallback = _fallback_workflow(conversation)
    agents = [
        {
            "id": it.agent.id,
            "name": it.agent.name,
            "type": it.agent.type,
            "description": it.agent.description,
            "capabilities": it.agent.capabilities,
        }
        for it in _active_participants(conversation)
        if it.agent
    ]
    instruction = (payload.instruction or payload.prompt or "").strip()
    effective_instruction = instruction or "根据当前群聊成员和职责生成一个可直接运行的协作工作流。"
    provider = await _model_provider(db)
    if _is_mock_model_provider(provider):
        raise ValidationAppError("AI 工作流生成需要先配置真实可用的模型，当前是 mock/未配置状态。")

    try:
        result = await provider.chat(
            messages=[
                ChatMessage(
                    role="system",
                    content=(
                        "你是 AgentHub 的工作流架构师。只返回一个 JSON 对象，不要 Markdown、不要解释。"
                        "JSON 必须包含 mode、output_mode、nodes、edges、settings。"
                        "nodes 是数组，每个节点必须包含 id、title、type、role、status、meta、config、position；"
                        "agent/review 节点必须使用 available_agents 中真实存在的 agent_id。"
                        "edges 是数组，使用 {id, source, target} 或 [source, target]。"
                        "允许的 type: start, agent, tool, skill, mcp, condition, loop, review, artifact, end。"
                        "必须至少包含一个 start、一个 end、一个 agent/review/tool/skill/mcp/artifact 可执行节点。"
                        "根据 instruction 和 Agent 能力设计真实流程，不要照抄 current_workflow。"
                    ),
                ),
                ChatMessage(
                    role="user",
                    content=json.dumps(
                        {
                            "instruction": effective_instruction,
                            "conversation": conversation_to_dict(conversation),
                            "available_agents": agents,
                            "current_workflow": fallback,
                            "response_schema": {
                                "mode": "ai_generated",
                                "output_mode": "independent_messages | aggregate",
                                "nodes": [
                                    {
                                        "id": "string",
                                        "title": "string",
                                        "type": "start | agent | review | tool | skill | mcp | condition | loop | artifact | end",
                                        "role": "string",
                                        "status": "ready",
                                        "meta": "string",
                                        "agent_id": "agent/review only, from available_agents",
                                        "config": {},
                                        "position": {"x": 80, "y": 120},
                                    }
                                ],
                                "edges": [{"id": "edge-id", "source": "node-id", "target": "node-id"}],
                                "settings": {"generation_instruction": effective_instruction},
                            },
                        },
                        ensure_ascii=False,
                    ),
                ),
            ],
            temperature=0.25,
            max_tokens=2400,
        )
    except Exception as exc:
        raise ValidationAppError(f"AI 工作流生成失败：{exc}") from exc

    raw = _parse_json_object(result.content)
    generated = _extract_workflow_object(raw)
    if not generated:
        raise ValidationAppError("AI 没有返回可用的 workflow JSON，请调整需求后重试。")

    workflow = _normalize_workflow(generated, conversation)
    workflow["settings"] = {
        **(workflow.get("settings") or {}),
        "generation_instruction": instruction,
        "generated_by_ai": True,
        "generated_by": "model",
        "model": getattr(provider, "model", None),
    }
    conversation.extra = {**(conversation.extra or {}), "workflow": workflow}
    await db.commit()
    return ok(workflow, "工作流已生成")



@router.get("/conversations/{conversation_id}/workflow/runs", response_model=ApiResponse[dict])
async def list_workflow_runs(
    conversation_id: str,
    latest: bool = False,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    query = (
        select(WorkflowRun)
        .where(WorkflowRun.conversation_id == conversation.id)
        .order_by(WorkflowRun.created_at.desc())
    )
    if latest:
        run = await db.scalar(query.limit(1))
        return ok(workflow_run_to_dict(run) if run else None)
    runs = (await db.scalars(query.limit(50))).all()
    return ok({"items": [workflow_run_to_dict(run) for run in runs], "total": len(runs)})


@router.post("/conversations/{conversation_id}/workflow/runs", response_model=ApiResponse[dict])
async def start_workflow_run(
    conversation_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    workflow = (conversation.extra or {}).get("workflow")
    if not isinstance(workflow, dict):
        workflow = _fallback_workflow(conversation)
    workflow = _normalize_workflow(
        payload.get("workflow") if isinstance(payload.get("workflow"), dict) else workflow,
        conversation,
    )
    agents = [item.agent for item in _active_participants(conversation) if item.agent]
    validation = validate_workflow_graph(workflow, agents=agents)
    if not validation.ok:
        raise ValidationAppError(format_workflow_validation_message(validation))

    run = WorkflowRun(
        conversation_id=conversation.id,
        trigger_message_id=payload.get("trigger_message_id"),
        started_by=user.id,
        status="running",
        mode=str(payload.get("mode") or workflow.get("mode") or "manual"),
        workflow_snapshot=workflow,
        node_states=_new_node_states(workflow),
        edge_states=build_edge_states(workflow),
        events=[{"type": "run.started", "at": utcnow().isoformat(), "actor_id": user.id}],
        progress=5,
        started_at=utcnow(),
    )
    db.add(run)
    await db.flush()
    _sync_workflow_runtime(conversation, run)
    await db.commit()
    await db.refresh(run)

    asyncio.create_task(
        _execute_manual_workflow_run(
            conversation_id=conversation.id,
            run_id=run.id,
            prompt=str(payload.get("prompt") or _manual_workflow_prompt(workflow)),
        )
    )
    return ok(workflow_run_to_dict(run), "Workflow run started")


@router.patch("/conversations/{conversation_id}/workflow/runs/{run_id}/nodes/{node_id}", response_model=ApiResponse[dict])
async def update_workflow_node_state(
    conversation_id: str,
    run_id: str,
    node_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    run = await db.get(WorkflowRun, run_id)
    if not run or run.conversation_id != conversation.id:
        raise NotFoundError("Workflow run not found")

    states = deepcopy(run.node_states or [])
    found = False
    now = utcnow().isoformat()
    for state in states:
        if state.get("id") != node_id:
            continue
        found = True
        next_status = str(payload.get("status") or state.get("status") or "running")
        state["status"] = next_status
        if "progress" in payload:
            state["progress"] = max(0, min(100, int(payload.get("progress") or 0)))
        elif next_status in {"completed", "succeeded"}:
            state["progress"] = 100
        state["output"] = payload.get("output", state.get("output") or {})
        if state.get("type") == "condition" and "matched_branch" in payload:
            state["output"] = {**(state.get("output") or {}), "matched_branch": payload.get("matched_branch")}
        if state.get("type") == "loop":
            loop_output = dict(state.get("output") or {})
            if "current_iteration" in payload:
                loop_output["current_iteration"] = max(0, int(payload.get("current_iteration") or 0))
            if "max_iterations" in payload:
                loop_output["max_iterations"] = max(1, int(payload.get("max_iterations") or 1))
            state["output"] = loop_output
        state["message"] = payload.get("message", state.get("message"))
        if next_status in {"running", "reviewing"} and not state.get("started_at"):
            state["started_at"] = now
        if next_status in {"completed", "succeeded", "failed", "skipped"}:
            state["completed_at"] = now
        break
    if not found:
        raise NotFoundError("Workflow node not found")

    completed = len([state for state in states if state.get("status") in {"completed", "succeeded", "skipped"}])
    failed = any(state.get("status") == "failed" for state in states)
    total = max(1, len(states))
    run.node_states = states
    mark_json_field_modified(run, "node_states")
    run.progress = int((completed / total) * 100)
    if failed:
        run.status = "failed"
        run.completed_at = utcnow()
    elif completed == total:
        run.status = "completed"
        run.progress = 100
        run.completed_at = utcnow()
    else:
        run.status = "running"
    append_run_event(
        run,
        "node.updated",
        {
            "node_id": node_id,
            "status": payload.get("status"),
            "actor_id": user.id,
        },
    )
    _sync_workflow_runtime(conversation, run)
    await db.commit()
    await db.refresh(run)
    return ok(workflow_run_to_dict(run), "Workflow node state updated")


@router.patch("/conversations/{conversation_id}", response_model=ApiResponse[dict])
async def update_conversation(
    conversation_id: str,
    payload: UpdateConversationRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok(
        conversation_to_dict(await _patch(db, user, conversation_id, payload.model_dump())),
        "操作成功",
    )


@router.delete("/conversations/{conversation_id}", response_model=ApiResponse[dict])
async def delete_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    conversation = await _get(db, user, conversation_id)
    from app.services.conversation_run_manager import ConversationRunManager

    await ConversationRunManager.get_instance().close_conversation(conversation_id)
    conversation.deleted_at = utcnow()
    conversation.status = "deleted"
    await db.commit()
    return ok({"id": conversation.id, "deleted_at": conversation.deleted_at.isoformat()})


@router.post("/conversations/{conversation_id}/read", response_model=ApiResponse[dict])
async def mark_read(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    conversation = await _get(db, user, conversation_id)
    conversation.unread_count = 0
    await db.commit()
    return ok({"id": conversation.id, "unread_count": 0})


@router.get("/conversations/{conversation_id}/participants", response_model=ApiResponse[dict])
@router.get("/conversations/{conversation_id}/members", response_model=ApiResponse[dict])
async def list_participants(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    conversation = await _get(db, user, conversation_id)
    return ok({"items": [participant_to_dict(it) for it in _active_participants(conversation)]})


@router.post("/conversations/{conversation_id}/participants", response_model=ApiResponse[dict])
@router.post("/conversations/{conversation_id}/members", response_model=ApiResponse[dict])
async def add_participants(
    conversation_id: str,
    payload: AddParticipantRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    existing_agent_ids = {it.agent_id for it in _active_participants(conversation) if it.agent_id}
    existing_user_ids = {it.user_id for it in _active_participants(conversation) if it.user_id}
    current_agents = len(existing_agent_ids)
    add_agents = [aid for aid in payload.agent_ids if aid not in existing_agent_ids]
    if current_agents + len(add_agents) > 8:
        raise ValidationAppError("会话最多支持8个Agent")
    added: list[ConversationParticipant] = []
    if add_agents:
        agents = (
            await db.scalars(
                select(Agent).where(Agent.id.in_(add_agents), Agent.deleted_at.is_(None))
            )
        ).all()
        found = {a.id for a in agents}
        missing = set(add_agents) - found
        if missing:
            raise NotFoundError(f"Agent不存在：{', '.join(sorted(missing))}")
        # 查找已存在但已删除的 participants，恢复它们而不是创建新记录
        existing_removed = (
            await db.scalars(
                select(ConversationParticipant).where(
                    ConversationParticipant.conversation_id == conversation.id,
                    ConversationParticipant.agent_id.in_([a.id for a in agents]),
                    ConversationParticipant.left_at.is_not(None),
                )
            )
        ).all()
        removed_by_agent_id = {p.agent_id: p for p in existing_removed}
        for agent in agents:
            if agent.id in removed_by_agent_id:
                participant = removed_by_agent_id[agent.id]
                participant.left_at = None
                participant.role = payload.role
                participant.agent = agent
                added.append(participant)
            else:
                participant = ConversationParticipant(
                    conversation_id=conversation.id,
                    participant_type="agent",
                    agent_id=agent.id,
                    role=payload.role,
                )
                participant.agent = agent
                db.add(participant)
                added.append(participant)
    add_users = [uid for uid in payload.user_ids if uid not in existing_user_ids]
    if add_users:
        users = (
            await db.scalars(select(User).where(User.id.in_(add_users), User.deleted_at.is_(None)))
        ).all()
        for member in users:
            participant = ConversationParticipant(
                conversation_id=conversation.id,
                participant_type="user",
                user_id=member.id,
                nickname=member.display_name,
                role=payload.role,
            )
            db.add(participant)
            added.append(participant)
    if added:
        await db.flush()
        extra = dict(conversation.extra or {})
        if not isinstance(extra.get("workflow"), dict) or not (
            extra.get("workflow", {}).get("settings") or {}
        ).get("edited_by_user"):
            extra["workflow"] = _fallback_workflow(conversation)
            conversation.extra = extra
        conversation.last_message_preview = f"已加入 {len(added)} 位新成员"
        conversation.last_message_at = utcnow()
        conversation.activity_score += 3
        db.add(
            Message(
                conversation_id=conversation.id,
                sender_type="system",
                sender_name="System",
                content_type="event",
                content={"text": conversation.last_message_preview},
                status="completed",
            )
        )
    await db.commit()
    refreshed = await _get(db, user, conversation_id)
    return ok(conversation_to_dict(refreshed), "成员已加入")


@router.patch(
    "/conversations/{conversation_id}/participants/{participant_id}",
    response_model=ApiResponse[dict],
)
async def update_participant_role(
    conversation_id: str,
    participant_id: str,
    payload: ParticipantRoleUpdatePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    if payload.role not in {"owner", "admin", "member"}:
        raise ValidationAppError("角色必须是 owner/admin/member")
    participant = next((it for it in conversation.participants if it.id == participant_id), None)
    if not participant:
        raise NotFoundError("成员不存在")
    if payload.role == "owner":
        for it in conversation.participants:
            if it.role == "owner":
                it.role = "admin"
    participant.role = payload.role
    await db.commit()
    return ok(participant_to_dict(participant), "成员角色已更新")


@router.delete(
    "/conversations/{conversation_id}/participants/{participant_id}",
    response_model=ApiResponse[dict],
)
async def remove_participant(
    conversation_id: str,
    participant_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    participant = next(
        (it for it in conversation.participants if it.id == participant_id and it.left_at is None),
        None,
    )
    if not participant:
        raise NotFoundError("成员不存在")
    active_agents = [
        it for it in _active_participants(conversation) if it.participant_type == "agent"
    ]
    if participant.participant_type == "agent" and len(active_agents) <= 1:
        raise ValidationAppError("会话至少需要保留 1 个 Agent")
    if participant.participant_type != "agent" and participant.role == "owner":
        raise ValidationAppError("不能直接移除群主，请先转让群主")
    participant.left_at = utcnow()
    extra = dict(conversation.extra or {})
    if not isinstance(extra.get("workflow"), dict) or not (
        extra.get("workflow", {}).get("settings") or {}
    ).get("edited_by_user"):
        extra["workflow"] = _fallback_workflow(conversation)
        conversation.extra = extra
    conversation.last_message_preview = "群成员已移除"
    conversation.last_message_at = utcnow()
    db.add(
        Message(
            conversation_id=conversation.id,
            sender_type="system",
            sender_name="System",
            content_type="event",
            content={"text": conversation.last_message_preview},
            status="completed",
        )
    )
    await db.commit()
    return ok(conversation_to_dict(conversation), "成员已移除")


@router.post("/conversations/{conversation_id}/invites", response_model=ApiResponse[dict])
async def invite_participants(
    conversation_id: str,
    payload: InviteParticipantRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    token = f"invite-{conversation.id[:8]}-{utcnow().timestamp():.0f}"
    conversation.extra = {
        **(conversation.extra or {}),
        "last_invite": {
            "token": token,
            "invitee_email": payload.invitee_email,
            "agent_ids": payload.agent_ids,
            "role": payload.role,
            "status": "pending",
        },
    }
    await db.commit()
    return ok(
        {"invite_token": token, "status": "pending", "conversation_id": conversation.id},
        "邀请已创建",
    )


# compat routes
@compat_router.get("/conversations", response_model=dict)
async def compat_list_conversations(
    db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return {"items": await _list(db, user)}


@compat_router.post("/conversations", response_model=dict)
async def compat_create_conversation(
    payload: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return conversation_to_dict(await _create(db, user, payload.model_dump()))


@compat_router.get("/conversations/{conversation_id}/participants", response_model=dict)
@compat_router.get("/conversations/{conversation_id}/members", response_model=dict)
async def compat_list_participants(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    conversation = await _get(db, user, conversation_id)
    return {"items": [participant_to_dict(it) for it in _active_participants(conversation)]}


@compat_router.post("/conversations/{conversation_id}/participants", response_model=dict)
@compat_router.post("/conversations/{conversation_id}/members", response_model=dict)
async def compat_add_participants(
    conversation_id: str,
    payload: AddParticipantRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    existing = {it.agent_id for it in _active_participants(conversation) if it.agent_id}
    to_add = [aid for aid in payload.agent_ids if aid not in existing]
    agents = (
        await db.scalars(select(Agent).where(Agent.id.in_(to_add)))
    ).all()
    existing_removed = (
        await db.scalars(
            select(ConversationParticipant).where(
                ConversationParticipant.conversation_id == conversation.id,
                ConversationParticipant.agent_id.in_([a.id for a in agents]),
                ConversationParticipant.left_at.is_not(None),
            )
        )
    ).all()
    removed_by_agent_id = {p.agent_id: p for p in existing_removed}
    for agent in agents:
        if agent.id in removed_by_agent_id:
            participant = removed_by_agent_id[agent.id]
            participant.left_at = None
            participant.role = payload.role
            participant.agent = agent
        else:
            participant = ConversationParticipant(
                conversation_id=conversation.id,
                participant_type="agent",
                agent_id=agent.id,
                role=payload.role,
            )
            participant.agent = agent
            db.add(participant)
    await db.commit()
    return conversation_to_dict(await _get(db, user, conversation_id))


@compat_router.get("/conversations/{conversation_id}", response_model=dict)
async def compat_get_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    return conversation_to_dict(await _get(db, user, conversation_id))


@compat_router.patch("/conversations/{conversation_id}", response_model=dict)
async def compat_update_conversation(
    conversation_id: str,
    payload: UpdateConversationRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return conversation_to_dict(await _patch(db, user, conversation_id, payload.model_dump()))


@compat_router.delete("/conversations/{conversation_id}", response_model=dict)
async def compat_delete_conversation(
    conversation_id: str, db: AsyncSession = Depends(get_db), user: User = Depends(get_current_user)
):
    conversation = await _get(db, user, conversation_id)
    conversation.deleted_at = utcnow()
    conversation.status = "deleted"
    await db.commit()
    return {"id": conversation.id, "deleted_at": conversation.deleted_at.isoformat()}


@compat_router.delete("/conversations/{conversation_id}/participants/{participant_id}", response_model=dict)
@compat_router.delete("/conversations/{conversation_id}/members/{participant_id}", response_model=dict)
async def compat_remove_participant(
    conversation_id: str,
    participant_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    participant = next(
        (it for it in conversation.participants if it.id == participant_id and it.left_at is None),
        None,
    )
    if not participant:
        raise NotFoundError("成员不存在")
    active_agents = [
        it for it in _active_participants(conversation) if it.participant_type == "agent"
    ]
    if participant.participant_type == "agent" and len(active_agents) <= 1:
        raise ValidationAppError("会话至少需要保留 1 个 Agent")
    if participant.participant_type != "agent" and participant.role == "owner":
        raise ValidationAppError("不能直接移除群主，请先转让群主")
    participant.left_at = utcnow()
    extra = dict(conversation.extra or {})
    if not isinstance(extra.get("workflow"), dict) or not (
        extra.get("workflow", {}).get("settings") or {}
    ).get("edited_by_user"):
        extra["workflow"] = _fallback_workflow(conversation)
        conversation.extra = extra
    conversation.last_message_preview = "群成员已移除"
    conversation.last_message_at = utcnow()
    db.add(
        Message(
            conversation_id=conversation.id,
            sender_type="system",
            sender_name="System",
            content_type="event",
            content={"text": conversation.last_message_preview},
            status="completed",
        )
    )
    await db.commit()
    return conversation_to_dict(conversation)
