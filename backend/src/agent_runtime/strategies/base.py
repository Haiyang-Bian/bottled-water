"""
调度器基类

调度员是一种 Agent 角色，不是系统基础设施。
通过 LLM 推理做决策，但受看门狗约束。
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any

from ..core.types import AgentReport, SchedulingDecision


class Scheduler(ABC):
    """调度器基类"""

    def __init__(self, agents: Dict[str, Any] = None):
        self.agents = agents or {}
        self.decision_history: List[SchedulingDecision] = []

    def reset(self) -> None:
        """重置调度器状态，在新的 Run 启动时调用。"""
        self.decision_history.clear()

    @abstractmethod
    async def make_decision(
        self,
        blackboard: Dict[str, Any],
        agent_reports: List[AgentReport],
        conversation_context: Dict[str, Any]
    ) -> SchedulingDecision:
        """
        基于 Blackboard 和 Agent 报告做出调度决策
        """
        pass

    @abstractmethod
    async def resolve_conflict(
        self,
        conflict_type: str,
        conflicting_reports: List[AgentReport],
        blackboard: Dict[str, Any]
    ) -> SchedulingDecision:
        """解决 Agent 之间的冲突"""
        pass

    async def should_verify(
        self,
        agent_report: AgentReport,
        task_history: List[Dict]
    ) -> bool:
        """
        决定是否需要验证

        调度员只决定"要不要查"，具体查什么由验证 Agent 负责。
        """
        if agent_report.will.value == "complete":
            recent_errors = sum(1 for t in task_history[-5:] if t.get("error"))
            return recent_errors >= 2
        return False
