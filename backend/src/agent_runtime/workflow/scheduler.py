"""
Workflow 调度器

实现 Scheduler 接口，通过带环图遍历顺序返回调度决策。
整合 graph / state / conditions / replanner 模块。
"""

from __future__ import annotations

import json
from typing import Any

from common.logger import get_logger

from ..core.types import AgentConfig, AgentReport, SchedulingDecision
from ..strategies.base import Scheduler
from .graph import WorkflowGraph
from .nodes import is_control_node, node_agent_id, node_config, workflow_node_type
from .replanner import replan_workflow, sanitize_workflow, should_replan
from .state import WorkflowState

logger = get_logger(__name__)


class WorkflowScheduler(Scheduler):
    """DAG / 带环图 Workflow 调度器。

    实现 Scheduler 接口：
    - set_workflow_context() 注入 workflow 定义和用户 prompt
    - make_decision() 按图遍历顺序返回 assign / tool_call / complete 决策
    - 控制节点（start / end / condition）在调度器内部处理，不产出外部决策
    """

    def __init__(self, agents: dict[str, AgentConfig] | None = None):
        super().__init__(agents or {})
        self._graph: WorkflowGraph | None = None
        self._state: WorkflowState = WorkflowState()
        self._last_decision_node_id: str | None = None
        self._last_decision_type: str = ""
        self._last_parallel_node_ids: list[str] = []

    def reset(self) -> None:
        """重置 Workflow 遍历状态，支持策略实例跨 Run 复用。"""
        super().reset()
        self._state = WorkflowState()
        self._last_decision_node_id = None
        self._last_decision_type = ""
        self._last_parallel_node_ids = []
        if self._graph:
            start_node = self._graph.find_start_node()
            self._state.start(start_node)

    # ------------------------------------------------------------------
    # 上下文注入
    # ------------------------------------------------------------------

    def set_workflow_context(self, workflow: dict[str, Any], prompt: str) -> None:
        """设置 Workflow 执行上下文（在 make_decision 前调用）。

        同时扫描节点，为 tool/skill/mcp/artifact 节点创建虚拟工具智能体。
        """
        self._graph = WorkflowGraph(workflow)
        self._state = WorkflowState(
            workflow_id=workflow.get("conversation_id", ""),
            prompt=prompt,
        )
        self._ensure_tool_agents()
        start_node = self._graph.find_start_node()
        self._state.start(start_node)
        logger.info(
            "WorkflowScheduler 初始化",
            workflow_id=self._state.workflow_id,
            start_node=start_node,
            node_count=len(self._graph.nodes),
            agent_count=len(self.agents),
        )

    # ------------------------------------------------------------------
    # Scheduler 接口实现
    # ------------------------------------------------------------------

    async def make_decision(
        self,
        blackboard: dict[str, Any],
        agent_reports: list[AgentReport],
        conversation_context: dict[str, Any],
    ) -> SchedulingDecision:
        """基于图遍历返回下一个调度决策"""
        # 首次运行时从 conversation_context 注入用户 prompt
        if not self._state.prompt and conversation_context.get("current_task"):
            self._state.prompt = conversation_context["current_task"]

        while True:
            if self._last_parallel_node_ids:
                join_node_id = self._parallel_join_node()
                for node_id in self._last_parallel_node_ids:
                    node = self._graph.get_node(node_id) if self._graph else None
                    node_type = workflow_node_type(node)
                    self._state.get_or_create_node_state(node_id, node_type).mark_completed()
                    self._state.visited_nodes.add(node_id)
                self._last_parallel_node_ids = []
                self._state.current_node_id = join_node_id
                if not self._state.current_node_id:
                    self._state.complete()
                    return SchedulingDecision(
                        decision_type="complete", rationale="workflow 并行节点执行完毕"
                    )
                continue

            # ---- 1. 检查是否需要推进（上一节点已执行完成） ----
            current_node_id = self._state.current_node_id
            if (
                current_node_id
                and current_node_id == self._last_decision_node_id
            ):
                self._advance_node(blackboard)
                if not self._state.current_node_id:
                    self._state.complete()
                    return SchedulingDecision(
                        decision_type="complete", rationale="workflow 执行完毕"
                    )
                continue

            # ---- 2. 检查终止条件 ----
            if self._state.is_step_limit_reached():
                self._state.complete()
                return SchedulingDecision(
                    decision_type="complete", rationale="达到最大执行步数"
                )

            if not current_node_id:
                self._state.complete()
                return SchedulingDecision(
                    decision_type="complete", rationale="无可执行节点"
                )

            node = self._graph.get_node(current_node_id)
            if not node:
                self._state.complete()
                return SchedulingDecision(
                    decision_type="complete", rationale="当前节点不存在"
                )

            node_type = workflow_node_type(node)

            # ---- 3. 控制节点：内部处理 ----
            if is_control_node(node_type):
                self._state.record_visit(current_node_id)
                self._state.get_or_create_node_state(
                    current_node_id, node_type
                ).mark_completed()

                parallel_decision = self._build_parallel_decision_from_successors(current_node_id)
                if parallel_decision:
                    return parallel_decision

                next_id = self._graph.next_node(
                    current_node_id,
                    blackboard.get("kv_state", {}),
                    self._state.visited_nodes,
                )
                self._state.current_node_id = next_id
                if next_id:
                    continue
                self._state.complete()
                return SchedulingDecision(
                    decision_type="complete", rationale="无后续节点"
                )

            # ---- 4. 可执行节点：返回决策 ----
            self._state.record_visit(current_node_id)
            self._state.get_or_create_node_state(
                current_node_id, node_type
            ).mark_started()
            self._last_decision_node_id = current_node_id
            return self._build_decision(node, node_type, current_node_id)

    async def resolve_conflict(
        self,
        conflict_type: str,
        conflicting_reports: list[AgentReport],
        blackboard: dict[str, Any],
    ) -> SchedulingDecision:
        """Workflow 不处理 Agent 间冲突，直接完成"""
        return SchedulingDecision(
            decision_type="complete",
            rationale=f"Workflow 不处理冲突类型: {conflict_type}",
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _ensure_tool_agents(self) -> None:
        """扫描 workflow 节点，为 tool/skill/mcp/artifact 创建虚拟工具智能体。

        虚拟智能体仅用于统一代码结构，system_prompt 为空，工具权限按需配置。
        """
        from ..core.types import AgentConfig

        for node in self._graph.nodes:
            node_type = workflow_node_type(node)
            if node_type not in {"tool", "skill", "mcp", "artifact"}:
                continue

            config = node_config(node)
            node_id = str(node.get("id", ""))

            if node_type == "tool":
                tool_name = config.get("tool_name", "")
                virtual_id = f"__tool__{node_id}"
                self.agents[virtual_id] = AgentConfig(
                    id=virtual_id,
                    name=f"Tool:{tool_name}",
                    system_prompt="",
                    role="tool_executor",
                    tools=[tool_name] if tool_name else [],
                    model_config={"node_config": config, "node_type": node_type},
                )
            elif node_type == "skill":
                skill_id = config.get("skill_id", "")
                virtual_id = f"__skill__{node_id}"
                self.agents[virtual_id] = AgentConfig(
                    id=virtual_id,
                    name=f"Skill:{skill_id}",
                    system_prompt="",
                    role="skill_executor",
                    tools=[],
                    model_config={"node_config": config, "node_type": node_type},
                )
            elif node_type == "mcp":
                tool_name = config.get("tool_name", "")
                virtual_id = f"__mcp__{node_id}"
                self.agents[virtual_id] = AgentConfig(
                    id=virtual_id,
                    name=f"MCP:{tool_name}",
                    system_prompt="",
                    role="mcp_executor",
                    tools=[],
                    model_config={"node_config": config, "node_type": node_type},
                )
            elif node_type == "artifact":
                artifact_type = config.get("artifact_type", "html")
                virtual_id = f"__artifact__{node_id}"
                self.agents[virtual_id] = AgentConfig(
                    id=virtual_id,
                    name=f"Artifact:{artifact_type}",
                    system_prompt="",
                    role="artifact_executor",
                    tools=[],
                    model_config={"node_config": config, "node_type": node_type},
                )

            # 注入虚拟 agent_id 到节点配置
            node.setdefault("config", {})
            node["config"]["agent_id"] = virtual_id

    def _advance_node(self, blackboard: dict[str, Any]) -> None:
        """标记当前节点完成并推进到下一个节点"""
        current_node_id = self._state.current_node_id
        if not current_node_id:
            return

        node_state = self._state.node_states.get(current_node_id)
        if node_state:
            node_state.mark_completed()

        next_id = self._graph.next_node(
            current_node_id,
            blackboard.get("kv_state", {}),
            self._state.visited_nodes,
        )
        self._state.current_node_id = next_id
        self._last_decision_node_id = None
        self._last_decision_type = ""

    def _build_decision(
        self, node: dict[str, Any], node_type: str, node_id: str
    ) -> SchedulingDecision:
        """根据节点类型构建调度决策。

        所有可执行节点统一返回 assign 决策，由 Agent 执行。
        tool/skill/mcp/artifact 节点的工具信息通过 task_description 传递。
        """
        config = node_config(node)
        prompt = self._state.prompt

        # 获取 agent_id：优先节点配置，无则使用第一个可用 agent
        agent_id = node_agent_id(node)
        if not agent_id and self.agents:
            agent_id = next(iter(self.agents.keys()), "")

        # 构建 task_description
        node_title = node.get("title", "")
        node_meta = node.get("meta", "")

        if node_type in {"agent", "review"}:
            task_desc = f"{prompt}\n\nNode: {node_title}\n{node_meta}"
            rationale = f"workflow {node_type} node: {node_title}"
        elif node_type == "tool":
            task_desc = (
                f"{prompt}\n\n请调用工具: {config.get('tool_name', '')}\n"
                f"参数: {json.dumps(config.get('arguments', {}), ensure_ascii=False)}\n"
                f"{node_meta}"
            )
            rationale = f"workflow tool: {config.get('tool_name', '')}"
        elif node_type == "skill":
            task_desc = (
                f"{prompt}\n\n请执行 Skill: {config.get('skill_id', '')}\n"
                f"参数: {json.dumps(config.get('arguments', {}), ensure_ascii=False)}\n"
                f"{node_meta}"
            )
            rationale = f"workflow skill: {config.get('skill_id', '')}"
        elif node_type == "mcp":
            task_desc = (
                f"{prompt}\n\n请调用 MCP 工具: {config.get('tool_name', '')}\n"
                f"Server: {config.get('server_id', '')}\n"
                f"参数: {json.dumps(config.get('arguments', {}), ensure_ascii=False)}\n"
                f"{node_meta}"
            )
            rationale = f"workflow mcp: {config.get('tool_name', '')}"
        elif node_type == "artifact":
            task_desc = (
                f"{prompt}\n\n请生成产物: {node_title}\n"
                f"类型: {config.get('artifact_type', 'html')}\n"
                f"{node_meta}"
            )
            rationale = f"workflow artifact: {node_title}"
        else:
            task_desc = f"{prompt}\n\nNode: {node_title}\n{node_meta}"
            rationale = f"workflow node: {node_title}"

        self._last_decision_type = "assign"
        return SchedulingDecision(
            decision_type="assign",
            target_agent_id=agent_id or "",
            task_description=task_desc,
            rationale=rationale,
        )

    def _build_parallel_decision_from_successors(
        self,
        node_id: str,
    ) -> SchedulingDecision | None:
        if not self._graph:
            return None
        successors = [
            succ_id
            for succ_id in self._graph.get_successors(node_id)
            if succ_id not in self._state.visited_nodes
        ]
        if len(successors) < 2:
            return None

        target_agent_ids: list[str] = []
        executable_node_ids: list[str] = []
        for succ_id in successors:
            node = self._graph.get_node(succ_id)
            node_type = workflow_node_type(node)
            if is_control_node(node_type):
                continue
            agent_id = node_agent_id(node) if node else None
            if not agent_id or agent_id not in self.agents:
                continue
            self._state.record_visit(succ_id)
            self._state.get_or_create_node_state(succ_id, node_type).mark_started()
            executable_node_ids.append(succ_id)
            target_agent_ids.append(agent_id)

        target_agent_ids = list(dict.fromkeys(target_agent_ids))
        if len(target_agent_ids) < 2:
            return None

        self._last_parallel_node_ids = executable_node_ids
        self._last_decision_node_id = None
        self._last_decision_type = "parallel"
        self._state.current_node_id = None
        return SchedulingDecision(
            decision_type="parallel",
            target_agent_id=target_agent_ids[0],
            target_agent_ids=target_agent_ids,
            verification_agents=target_agent_ids,
            task_description=self._state.prompt,
            task=self._state.prompt,
            rationale="workflow 并行分支：同时调度多个 Agent 节点",
            action="assign",
            expected_outputs=["各 Agent 独立回复"],
        )

    def _parallel_join_node(self) -> str | None:
        if not self._graph or not self._last_parallel_node_ids:
            return None
        common: set[str] | None = None
        for node_id in self._last_parallel_node_ids:
            successors = set(self._graph.get_successors(node_id))
            common = successors if common is None else common & successors
        if not common:
            return None
        for node_id in common:
            node = self._graph.get_node(node_id)
            if workflow_node_type(node) == "end":
                return node_id
        return next(iter(common), None)

    # ------------------------------------------------------------------
    # 重排
    # ------------------------------------------------------------------

    async def maybe_replan(
        self,
        prompt: str,
        current_workflow: dict[str, Any],
        available_agents: list[dict[str, Any]],
        model_chat_fn=None,
    ) -> dict[str, Any] | None:
        """检查并重排 workflow。

        Args:
            prompt: 用户输入
            current_workflow: 当前 workflow
            available_agents: 可用 Agent 列表
            model_chat_fn: 模型调用函数

        Returns:
            新的 workflow 或 None（不重排）
        """
        if not should_replan(prompt):
            return None

        plan_data = replan_workflow(
            current_workflow=current_workflow,
            prompt=prompt,
            agents=available_agents,
            model_chat_fn=model_chat_fn,
        )
        if not plan_data:
            return None

        # 调用模型（由调用方提供异步函数）
        if model_chat_fn is not None:
            try:
                response = await model_chat_fn(
                    [
                        {
                            "role": "system",
                            "content": plan_data["system_prompt"],
                        },
                        {
                            "role": "user",
                            "content": plan_data["user_content"],
                        },
                    ],
                    temperature=0.1,
                    max_tokens=1800,
                )
                raw = self._parse_json(response.content if hasattr(response, "content") else str(response))
                if raw:
                    return sanitize_workflow(
                        raw,
                        conversation_id=current_workflow.get("conversation_id", ""),
                        available_agents=available_agents,
                    )
            except Exception as e:
                logger.warning("workflow 重排模型调用失败", error=str(e))

        return None

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        """从文本中提取 JSON 对象"""
        try:
            return json.loads(text.strip())
        except json.JSONDecodeError:
            pass
        match = __import__("re").search(r"\{.*\}", text, __import__("re").S)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        return None

    # ------------------------------------------------------------------
    # 属性
    # ------------------------------------------------------------------

    @property
    def workflow_state(self) -> WorkflowState:
        """当前 workflow 运行时状态（用于观测）"""
        return self._state

    @property
    def is_completed(self) -> bool:
        """workflow 是否已完成"""
        return self._state.status in {"completed", "failed", "cancelled"}

    def get_all_agents(self) -> dict[str, AgentConfig]:
        """获取所有 Agent（含虚拟工具 Agent）。

        供 OrchestratorService 创建 AgentSession 时使用。
        """
        return dict(self.agents)
