"""
Workflow 调度策略

委托自 agent_runtime.workflow 子模块。
外部导入路径保持不变：from agent_runtime.strategies.workflow import WorkflowScheduler
"""

from __future__ import annotations

from ..workflow.scheduler import WorkflowScheduler as _WorkflowScheduler

__all__ = ["WorkflowScheduler"]


class WorkflowScheduler(_WorkflowScheduler):
    """Workflow 调度策略（委托实现）。

    完整实现在 agent_runtime.workflow.scheduler 中。
    """

    pass
