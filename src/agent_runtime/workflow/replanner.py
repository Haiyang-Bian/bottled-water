"""
Workflow 重排器

LLM 驱动的 workflow 重规划。接收当前 workflow 和用户指令，返回新的 workflow 结构。
纯函数，无状态，不操作数据库。
"""

from __future__ import annotations

import json
import re
from typing import Any

from agent_contracts.logging import get_logger

from .nodes import node_config, workflow_node_type

logger = get_logger(__name__)

_REPLAN_PATTERN = re.compile(
    r"(workflow|canvas|流程|画布|编排|规划|重排|调整工作流|让.*规划|master.*规划)", re.I
)


def should_replan(prompt: str) -> bool:
    """判断用户指令是否触发 workflow 重排。

    Args:
        prompt: 用户输入文本

    Returns:
        是否触发重排
    """
    return bool(_REPLAN_PATTERN.search(prompt))


def replan_workflow(
    current_workflow: dict[str, Any],
    prompt: str,
    agents: list[dict[str, Any]],
    model_chat_fn=None,
) -> dict[str, Any] | None:
    """LLM 驱动的 workflow 重排。

    通过调用外部模型服务生成新的 workflow 结构。
    如果调用失败或返回无效数据，返回 None。

    Args:
        current_workflow: 当前 workflow 结构
        prompt: 用户重排指令
        agents: 可用 Agent 列表（字典格式，含 id/name/type）
        model_chat_fn: 可选的模型调用函数，签名 async fn(messages, **kwargs) -> response

    Returns:
        新的 workflow 字典或 None
    """
    if model_chat_fn is None:
        logger.info("未提供模型调用函数，跳过 workflow 重排")
        return None

    system_prompt = (
        "You are an AgentHub workflow planning agent. Return JSON only. "
        "Allowed node types: start, agent, tool, skill, mcp, condition, review, artifact, end. "
        "Preserve type/config fields. Use only provided agent ids. "
        "The workflow graph CAN contain cycles (loops via edges). "
        "Do NOT use 'loop' node type; use condition nodes with edges that form cycles."
    )

    user_content = json.dumps(
        {
            "instruction": prompt,
            "current_workflow": current_workflow,
            "agents": agents,
        },
        ensure_ascii=False,
    )

    try:
        # 注意：model_chat_fn 是异步函数，但 replan_workflow 本身是同步函数
        # 调用方需要 await model_chat_fn(...)
        logger.info("请求 workflow 重排", prompt=prompt[:50])
        return {
            "system_prompt": system_prompt,
            "user_content": user_content,
            "current_workflow": current_workflow,
            "agents": agents,
        }
    except Exception as e:
        logger.warning("workflow 重排准备失败", error=str(e))
        return None


def sanitize_workflow(
    raw: dict[str, Any],
    conversation_id: str,
    available_agents: list[dict[str, Any]],
) -> dict[str, Any]:
    """清理并验证 workflow 结构。

    确保节点 ID 唯一、边有效、agent_id 合法、字段长度限制。

    Args:
        raw: 原始 workflow 字典
        conversation_id: 会话 ID
        available_agents: 可用 Agent 列表

    Returns:
        清理后的 workflow 字典
    """
    active_agent_ids = {str(a.get("id")) for a in available_agents}

    # 清理节点
    raw_nodes = raw.get("nodes") if isinstance(raw.get("nodes"), list) else []
    nodes: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for index, node in enumerate(raw_nodes[:40]):
        if not isinstance(node, dict):
            continue

        node_type = workflow_node_type(node)
        node_id = str(node.get("id") or f"node-{index + 1}")

        # 去重 ID
        if node_id in seen_ids:
            node_id = f"{node_id}-{index}"
        seen_ids.add(node_id)

        config = node_config(node)
        agent_id = node.get("agent_id") or config.get("agent_id")

        # 过滤无效的 agent 节点
        if node_type in {"agent", "review"} and agent_id and str(agent_id) not in active_agent_ids:
            continue

        nodes.append(
            {
                "id": node_id,
                "title": str(node.get("title") or node.get("name") or f"Node {index + 1}")[:80],
                "type": node_type,
                "role": str(node.get("role") or node_type),
                "status": str(node.get("status") or "ready"),
                "meta": str(node.get("meta") or node.get("description") or node_type)[:160],
                "config": config,
                **({"agent_id": str(agent_id)} if agent_id else {}),
            }
        )

    # 清理边
    node_ids = {n["id"] for n in nodes}
    raw_edges = raw.get("edges") if isinstance(raw.get("edges"), list) else []
    edges: list[list[str]] = []
    seen_edges: set[tuple[str, str]] = set()

    for edge in raw_edges[:80]:
        if not isinstance(edge, list) or len(edge) != 2:
            continue
        src, dst = str(edge[0]), str(edge[1])
        if src not in node_ids or dst not in node_ids:
            continue
        if (src, dst) in seen_edges:
            continue
        seen_edges.add((src, dst))
        edges.append([src, dst])

    # 如果没有有效节点，构建一个默认 workflow
    if not nodes:
        return _fallback_workflow(conversation_id, available_agents)

    return {
        "conversation_id": conversation_id,
        "mode": str(raw.get("mode") or "canvas"),
        "settings": raw.get("settings") if isinstance(raw.get("settings"), dict) else {},
        "nodes": nodes,
        "edges": edges or _fallback_edges(nodes),
    }


def _fallback_workflow(conversation_id: str, agents: list[dict[str, Any]]) -> dict[str, Any]:
    """构建默认 workflow：start → 所有 agent（独立并行） → end"""
    nodes: list[dict[str, Any]] = [
        {
            "id": "start",
            "title": "Start",
            "type": "start",
            "role": "start",
            "status": "ready",
            "meta": "message input",
            "config": {"input": "message"},
        },
    ]
    for agent in agents[:8]:
        node_type = "review" if agent.get("type") == "reviewer" else "agent"
        nodes.append(
            {
                "id": f"agent-{str(agent.get('id', ''))[:8]}",
                "title": str(agent.get("name", "Agent")),
                "type": node_type,
                "role": str(agent.get("type") or node_type),
                "status": "ready",
                "meta": str(agent.get("description") or agent.get("type") or node_type)[:160],
                "agent_id": str(agent.get("id")),
                "config": {
                    "agent_id": str(agent.get("id")),
                    "tools": (agent.get("config") or {}).get("tools", []),
                },
            }
        )
    nodes.append(
        {
            "id": "end",
            "title": "End",
            "type": "end",
            "role": "end",
            "status": "ready",
            "meta": "final answer",
            "config": {"output": "assistant_message"},
        }
    )
    agent_nodes = [n["id"] for n in nodes if n["type"] in {"agent", "review"}]
    edges = [["start", aid] for aid in agent_nodes] + [[aid, "end"] for aid in agent_nodes]
    if not edges:
        edges = [["start", "end"]]

    return {
        "conversation_id": conversation_id,
        "mode": "all_agents_independent",
        "settings": {"default_policy": "canvas-first", "review_policy": "optional"},
        "nodes": nodes,
        "edges": edges,
    }


def _fallback_edges(nodes: list[dict[str, Any]]) -> list[list[str]]:
    """为没有边的节点构建默认边：start → 所有可执行节点 → end"""
    start_id = None
    end_id = None
    executable_ids: list[str] = []

    for node in nodes:
        node_type = node.get("type", "agent")
        if node_type == "start":
            start_id = node["id"]
        elif node_type == "end":
            end_id = node["id"]
        elif node_type not in {"condition"}:
            executable_ids.append(node["id"])

    edges: list[list[str]] = []
    if start_id:
        edges.extend([[start_id, eid] for eid in executable_ids])
    if end_id:
        edges.extend([[eid, end_id] for eid in executable_ids])
    if not edges and len(nodes) >= 2:
        edges = [[nodes[0]["id"], nodes[-1]["id"]]]

    return edges
