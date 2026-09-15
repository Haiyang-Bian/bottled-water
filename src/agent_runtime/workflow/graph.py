"""
带环 Workflow 图遍历引擎

从 workflow 定义构建图结构，支持带环遍历、条件分支路由、visited 管理。
"""

from __future__ import annotations

from typing import Any

from agent_contracts.logging import get_logger

from .conditions import evaluate_condition
from .nodes import node_config, workflow_node_type

logger = get_logger(__name__)


class WorkflowGraph:
    """Workflow 图结构"""

    def __init__(self, workflow: dict[str, Any]):
        self.workflow = workflow
        self.nodes: list[dict[str, Any]] = [
            node for node in workflow.get("nodes", []) if isinstance(node, dict)
        ]
        self.node_by_id: dict[str, dict[str, Any]] = {
            str(n.get("id")): n for n in self.nodes if n.get("id")
        }
        self.edges: list[list[str]] = []
        self.outgoing: dict[str, list[str]] = {}
        self.incoming: dict[str, list[str]] = {}
        self._build_graph()

    def _build_graph(self) -> None:
        """构建图的邻接表"""
        for node_id in self.node_by_id:
            self.outgoing[node_id] = []
            self.incoming[node_id] = []

        raw_edges = self.workflow.get("edges") if isinstance(self.workflow.get("edges"), list) else []
        for edge in raw_edges:
            if not isinstance(edge, list) or len(edge) != 2:
                continue
            src, dst = str(edge[0]), str(edge[1])
            if src in self.node_by_id and dst in self.node_by_id:
                self.edges.append([src, dst])
                self.outgoing.setdefault(src, []).append(dst)
                self.incoming.setdefault(dst, []).append(src)

    def find_start_node(self) -> str | None:
        """查找 start 节点，找不到则返回第一个节点"""
        for node_id, node in self.node_by_id.items():
            if workflow_node_type(node) == "start":
                return node_id
        return next(iter(self.node_by_id.keys()), None)

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """获取节点"""
        return self.node_by_id.get(node_id)

    def get_successors(self, node_id: str) -> list[str]:
        """获取节点的后继节点 ID 列表"""
        return list(self.outgoing.get(node_id, []))

    def get_predecessors(self, node_id: str) -> list[str]:
        """获取节点的前驱节点 ID 列表"""
        return list(self.incoming.get(node_id, []))

    def has_node(self, node_id: str) -> bool:
        """判断节点是否存在"""
        return node_id in self.node_by_id

    def next_node(
        self,
        current_node_id: str,
        context: dict[str, Any],
        visited: set[str],
    ) -> str | None:
        """确定下一个要执行的节点。

        规则：
        1. 获取当前节点的所有后继节点
        2. 如果是 condition 节点，根据表达式求值选择分支
        3. 过滤掉已 visited 的节点（防止循环）
        4. 返回第一个可用的后继节点

        Args:
            current_node_id: 当前节点 ID
            context: 变量上下文（Blackboard kv_state）
            visited: 已访问节点集合

        Returns:
            下一个节点 ID 或 None
        """
        successors = self.get_successors(current_node_id)
        if not successors:
            return None

        current_node = self.get_node(current_node_id)
        node_type = workflow_node_type(current_node)

        # condition 节点：根据表达式求值选择分支
        if node_type == "condition" and current_node:
            config = node_config(current_node)
            expression = config.get("expression", "true")
            branches = config.get("branches", ["true", "false"])

            matched_branch = "true" if evaluate_condition(expression, context) else "false"
            if matched_branch in branches:
                branch_index = branches.index(matched_branch)
                if branch_index < len(successors):
                    return successors[branch_index]
            # 表达式结果不匹配任何分支，默认走第一条边
            return successors[0]

        # 普通节点：返回第一个未访问的后继
        for succ_id in successors:
            if succ_id not in visited:
                return succ_id

        # 所有后继都已访问，说明是循环，返回 None 终止
        return None

    def all_executable_nodes(self) -> list[str]:
        """获取所有可执行节点的 ID 列表"""
        return [
            node_id
            for node_id, node in self.node_by_id.items()
            if workflow_node_type(node) not in {"start", "end", "condition"}
        ]

    def to_dict(self) -> dict[str, Any]:
        """序列化为字典"""
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "node_ids": list(self.node_by_id.keys()),
        }
