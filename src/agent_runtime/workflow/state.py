"""
Workflow 运行时状态管理

管理节点执行状态、当前位置、已访问节点、执行轨迹。
纯内存状态，不持久化到数据库（用事件流替代）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from datetime import datetime


@dataclass
class NodeState:
    """单个节点的运行时状态"""

    node_id: str
    node_type: str
    status: str = "pending"  # pending | running | completed | skipped | failed
    output: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    retry_count: int = 0

    def mark_started(self) -> None:
        """标记为运行中"""
        self.status = "running"
        self.started_at = datetime.utcnow().isoformat()

    def mark_completed(self, output: dict[str, Any] | None = None) -> None:
        """标记为已完成"""
        self.status = "completed"
        self.completed_at = datetime.utcnow().isoformat()
        if output is not None:
            self.output.update(output)

    def mark_failed(self, error: str) -> None:
        """标记为失败"""
        self.status = "failed"
        self.completed_at = datetime.utcnow().isoformat()
        self.output["error"] = error

    def mark_skipped(self) -> None:
        """标记为跳过"""
        self.status = "skipped"
        self.completed_at = datetime.utcnow().isoformat()

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于事件流）"""
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "status": self.status,
            "output": self.output,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "retry_count": self.retry_count,
        }


@dataclass
class WorkflowState:
    """Workflow 整体运行时状态"""

    workflow_id: str = ""
    status: str = "pending"  # pending | running | completed | failed | cancelled
    current_node_id: str | None = None
    node_states: dict[str, NodeState] = field(default_factory=dict)
    visited_nodes: set[str] = field(default_factory=set)
    execution_trace: list[str] = field(default_factory=list)
    step_count: int = 0
    max_steps: int = 50
    prompt: str = ""
    started_at: str | None = None
    completed_at: str | None = None

    def start(self, start_node_id: str | None = None) -> None:
        """启动 workflow"""
        self.status = "running"
        self.current_node_id = start_node_id
        self.started_at = datetime.utcnow().isoformat()

    def complete(self) -> None:
        """标记 workflow 完成"""
        self.status = "completed"
        self.completed_at = datetime.utcnow().isoformat()

    def fail(self, error: str) -> None:
        """标记 workflow 失败"""
        self.status = "failed"
        self.completed_at = datetime.utcnow().isoformat()
        self.node_states["__workflow__"] = NodeState(
            node_id="__workflow__",
            node_type="workflow",
            status="failed",
            output={"error": error},
        )

    def get_or_create_node_state(self, node_id: str, node_type: str) -> NodeState:
        """获取或创建节点状态"""
        if node_id not in self.node_states:
            self.node_states[node_id] = NodeState(node_id=node_id, node_type=node_type)
        return self.node_states[node_id]

    def record_visit(self, node_id: str) -> None:
        """记录节点访问"""
        self.visited_nodes.add(node_id)
        self.execution_trace.append(node_id)
        self.step_count += 1

    def has_visited(self, node_id: str) -> bool:
        """判断节点是否已访问"""
        return node_id in self.visited_nodes

    def is_step_limit_reached(self) -> bool:
        """判断是否达到最大步数限制"""
        return self.step_count >= self.max_steps

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典（用于事件流）"""
        return {
            "workflow_id": self.workflow_id,
            "status": self.status,
            "current_node_id": self.current_node_id,
            "node_states": {k: v.to_dict() for k, v in self.node_states.items()},
            "visited_nodes": list(self.visited_nodes),
            "execution_trace": self.execution_trace,
            "step_count": self.step_count,
            "max_steps": self.max_steps,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }
