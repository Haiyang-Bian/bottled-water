"""
agent_runtime - 多智能体运行时

纯 Python 库，不依赖任何 Web 框架。
可独立使用，也可被 FastAPI/Flask/其他框架集成。

Runtime Kernel V1 的公开入口是 RuntimeEngine、RunRequest 与 RunHandle。
"""

from .core.types import (
    AgentConfig,
    AgentState,
    AgentWill,
    AgentReport,
    SchedulingDecision,
    Event,
    Message,
    ToolCall,
    ToolResult,
)
from .core.interfaces import (
    AgentContextBuildRequest,
    AgentContextBuildResult,
    AgentContextProvider,
    PersistenceBackend,
    EventSink,
    ToolExecutor,
)
from .runtime.session import Session
from .runtime.agent_actor import AgentActor
from .runtime.actor_orchestrator import ActorOrchestrator
from .runtime.event_dispatcher import EventDispatcher
from .runtime.mailbox import Mailbox
from .runtime.watchdog import Watchdog, WatchdogConfig
from .runtime.engine import RuntimeEngine, RunHandle
from .runtime.cancellation import CancellationScope, RunLease
from .core.run_types import (
    AgentMemory,
    ContextDelta,
    ContextSnapshot,
    EventEnvelope,
    RunRequest,
    RunResult,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    Usage,
)
from .strategies.scheduler_agent import SchedulerAgent
from .strategies.base import Scheduler
from .strategies.tech_lead import TechLeadScheduler
from .strategies.single_agent import SingleAgentScheduler
from .context.blackboard import BlackboardManager
from .context.agent_ctx import AgentContextManager, AgentContext
from .tools.registry import ToolRegistry
from .tools.executor import ToolExecutorImpl

__all__ = [
    # 核心类型
    "AgentConfig",
    "AgentState",
    "AgentWill",
    "AgentReport",
    "SchedulingDecision",
    "Event",
    "Message",
    "ToolCall",
    "ToolResult",
    # 接口
    "PersistenceBackend",
    "EventSink",
    "ToolExecutor",
    "AgentContextBuildRequest",
    "AgentContextBuildResult",
    "AgentContextProvider",
    # 运行时
    "Session",
    "AgentActor",
    "ActorOrchestrator",
    "EventDispatcher",
    "Mailbox",
    "Watchdog",
    "WatchdogConfig",
    "RuntimeEngine",
    "RunHandle",
    "RunRequest",
    "RunResult",
    "RunSnapshot",
    "RunState",
    "RuntimeLimits",
    "EventEnvelope",
    "ContextSnapshot",
    "ContextDelta",
    "AgentMemory",
    "SchedulingProposal",
    "Usage",
    "CancellationScope",
    "RunLease",
    # 调度策略
    "Scheduler",
    "TechLeadScheduler",
    "SchedulerAgent",
    "SingleAgentScheduler",
    # 上下文管理
    "BlackboardManager",
    "AgentContextManager",
    "AgentContext",
    # 工具
    "ToolRegistry",
    "ToolExecutorImpl",
]
