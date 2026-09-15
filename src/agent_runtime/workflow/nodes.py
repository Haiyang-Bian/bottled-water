"""
Workflow 节点类型与节点工具函数

纯工具模块，无状态，无外部依赖。
"""

from __future__ import annotations

from typing import Any

WORKFLOW_NODE_TYPES = frozenset(
    {"start", "agent", "tool", "skill", "mcp", "condition", "review", "artifact", "end"}
)


def workflow_node_type(node: dict[str, Any] | None) -> str:
    """规范化节点类型。

    Args:
        node: 节点字典，可能为 None

    Returns:
        规范化后的节点类型字符串
    """
    if not isinstance(node, dict):
        return "agent"
    raw = str(node.get("type") or node.get("role") or "agent").lower().strip()
    if raw in WORKFLOW_NODE_TYPES:
        return raw
    if raw in {"reviewer", "review"}:
        return "review"
    if raw in {"deploy", "delivery", "publish"}:
        return "artifact"
    if raw in {"input"}:
        return "start"
    return "agent"


def node_config(node: dict[str, Any]) -> dict[str, Any]:
    """提取并规范化节点配置。

    合并节点顶层字段与 config 子字典，确保各类型节点都有默认配置。

    Args:
        node: 节点字典

    Returns:
        规范化后的配置字典
    """
    config = dict(node.get("config") if isinstance(node.get("config"), dict) else {})

    if node.get("agent_id"):
        config.setdefault("agent_id", node.get("agent_id"))

    node_type = workflow_node_type(node)

    if node_type == "condition":
        config.setdefault("expression", "true")
        config.setdefault("branches", ["true", "false"])
    elif node_type == "tool":
        config.setdefault("tool_name", node.get("tool_name") or "")
        config.setdefault("arguments", node.get("arguments") or {})
    elif node_type == "mcp":
        config.setdefault("server_id", node.get("server_id") or "")
        config.setdefault("tool_name", node.get("tool_name") or "")
        config.setdefault("arguments", node.get("arguments") or {})
    elif node_type == "skill":
        config.setdefault("skill_id", node.get("skill_id") or "")
        config.setdefault("arguments", node.get("arguments") or {})
    elif node_type == "artifact":
        config.setdefault("artifact_type", node.get("artifact_type") or "html")

    return config


def node_agent_id(node: dict[str, Any]) -> str | None:
    """提取节点关联的 Agent ID。

    优先从节点顶层 agent_id 提取，其次从 config.agent_id 提取。

    Args:
        node: 节点字典

    Returns:
        Agent ID 或 None
    """
    if not isinstance(node, dict):
        return None
    agent_id = node.get("agent_id")
    if agent_id:
        return str(agent_id)
    config = node.get("config")
    if isinstance(config, dict):
        config_agent_id = config.get("agent_id")
        if config_agent_id:
            return str(config_agent_id)
    return None


def is_executable_node(node_type: str) -> bool:
    """判断节点是否需要外部执行（agent / tool / review / skill / mcp / artifact）。

    start / end / condition 为控制节点，由调度器内部处理。

    Args:
        node_type: 节点类型

    Returns:
        是否需要外部执行
    """
    return node_type in {"agent", "tool", "review", "skill", "mcp", "artifact"}


def is_control_node(node_type: str) -> bool:
    """判断节点是否为控制节点（调度器内部处理，不产出外部决策）。

    Args:
        node_type: 节点类型

    Returns:
        是否为控制节点
    """
    return node_type in {"start", "end", "condition"}
