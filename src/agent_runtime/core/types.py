"""
运行时核心类型

使用标准库 dataclasses，零框架依赖。
"""

from dataclasses import dataclass, field
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
from enum import Enum


class AgentState(str, Enum):
    """Agent 运行状态"""

    IDLE = "idle"
    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    WAITING = "waiting"
    COMPLETED = "completed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class AgentWill(str, Enum):
    """Agent 意图"""

    EXECUTE = "execute"
    WAIT = "wait"
    DELEGATE = "delegate"
    COMPLETE = "complete"
    BLOCKED = "blocked"


@dataclass
class AgentConfig:
    """Agent 配置"""

    id: str
    name: str
    system_prompt: str
    role: str = "worker"  # "leader" | "worker" | "verifier"
    model_config: Dict[str, Any] = field(default_factory=dict)
    tools: List[str] = field(default_factory=list)


@dataclass
class AgentReport:
    """Agent 状态报告"""

    agent_id: str
    state: AgentState
    will: AgentWill
    target_task: Optional[str] = None
    blockers: List[str] = field(default_factory=list)
    priority: int = 0
    confidence: float = 1.0
    rationale: str = ""
    expected_duration: Optional[int] = None


@dataclass
class SchedulingDecision:
    """调度决策"""

    decision_type: str  # "assign" | "parallel" | "wait" | "escalate" | "user_input"
    target_agent_id: Optional[str] = None
    task_description: str = ""
    rationale: str = ""
    requires_verification: bool = False
    verification_agents: List[str] = field(default_factory=list)
    action: str = ""
    target_agent_ids: List[str] = field(default_factory=list)
    task: str = ""
    expected_outputs: List[str] = field(default_factory=list)
    requires_review: bool = False
    fallback_reason: str = ""


@dataclass
class Message:
    """运行时消息"""

    id: str
    conversation_id: str
    agent_id: Optional[str]
    content: str
    role: str  # "user" | "assistant" | "system" | "tool"
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class Event:
    """运行时事件

    扩展字段支持全过程可观测：
    - source: 事件来源（orchestrator/agent:coder/watchdog 等）
    - target: 定向投递目标，None 表示广播
    - channel: 可见性通道（all/internal/user）
    - correlation_id: 请求-响应匹配 ID
    """

    type: str
    payload: Dict[str, Any]
    source: str = "system"
    target: Optional[str] = None
    channel: str = "all"
    correlation_id: Optional[str] = None
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ToolCall:
    """工具调用"""

    tool_name: str
    parameters: Dict[str, Any]
    call_id: str

    @classmethod
    def new(cls, tc: Dict[str, Any]):
        function_info = tc.get("function", {})
        tool_name = function_info.get("name", "")
        arguments_str = function_info.get("arguments", "{}")
        call_id = tc.get("id", "")

        err = None

        # 解析参数
        try:
            if isinstance(arguments_str, str):
                parameters = json.loads(arguments_str)
            else:
                parameters = arguments_str
        except json.JSONDecodeError as e:
            parameters = {}
            err = e

        return cls(
            tool_name=tool_name,
            parameters=parameters,
            call_id=call_id,
        ), err


@dataclass
class ToolResult:
    """工具执行结果"""

    call_id: str
    success: bool
    result: Any
    error: Optional[str] = None
