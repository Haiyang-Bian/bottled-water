"""
Agent 私有上下文管理器

每个 Agent 有自己的"帧列表"，记录按时间顺序追加的上下文片段。
支持追加、查找、归档、清理。

注意：这不是栈（LIFO）。上下文按时间顺序追加，
需要支持随机查找（如找最近的 tool_result），且受 Token 预算约束。
"""

from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field
from datetime import datetime

from agent_contracts.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ContextFrame:
    """上下文帧"""

    frame_type: str  # "task" | "thought" | "tool_call" | "tool_result"
    content: Any
    timestamp: datetime = field(default_factory=datetime.utcnow)


class AgentContext:
    """Agent 私有上下文

    使用帧列表（frames）按时间顺序记录上下文，而非栈。
    支持：
    - 按时间追加帧
    - 查找最近某类型的帧
    - 按 Token 预算截断
    - 归档和清理
    """

    def __init__(self, agent_id: str, conversation_id: str):
        self.agent_id = agent_id
        self.conversation_id = conversation_id
        self.system_prompt: str = ""
        self.role_config: Dict[str, Any] = {}
        self.frames: List[ContextFrame] = []
        self.current_round: int = 0
        self.current_task: str = ""

    def add(self, frame_type: str, content: Any):
        """追加帧（按时间顺序）"""
        self.frames.append(ContextFrame(frame_type=frame_type, content=content))

    def find_last(self, frame_type: str = None) -> Optional[ContextFrame]:
        """查找最近一个（某类型的）帧"""
        if not self.frames:
            return None
        if frame_type:
            for frame in reversed(self.frames):
                if frame.frame_type == frame_type:
                    return frame
            return None
        return self.frames[-1]

    def find_all(self, frame_type: str) -> List[ContextFrame]:
        """查找所有某类型的帧"""
        return [f for f in self.frames if f.frame_type == frame_type]

    def get_full_context(self) -> str:
        """获取完整上下文文本（用于 LLM 调用）"""
        parts = [f"系统提示：{self.system_prompt}\n"]
        for frame in self.frames:
            if frame.frame_type == "task":
                parts.append(f"当前任务：{frame.content}\n")
            elif frame.frame_type == "thought":
                parts.append(f"思考：{frame.content}\n")
            elif frame.frame_type == "tool_call":
                parts.append(f"工具调用：{frame.content}\n")
            elif frame.frame_type == "tool_result":
                parts.append(f"工具返回：{frame.content}\n")
        return "\n".join(parts)

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """估算文本的 Token 数量（简化版，无 tiktoken 依赖）"""
        import re

        if not text:
            return 0
        cn_chars = len(re.findall(r"[一-鿿]", text))
        en_words = len(re.findall(r"[a-zA-Z]+", text))
        return cn_chars + int(en_words * 1.3) + 10

    def trim(self, max_tokens: int = 4000):
        """按 Token 预算滑动窗口截断，优先保留最近帧。

        策略：
        - 保留 system_prompt（由调用方控制）
        - 保留所有 task 帧（任务定义不可丢）
        - 从旧到新丢弃早期帧，直到 Token 预算内
        """
        if not self.frames:
            return

        current_tokens = self._estimate_tokens(self.system_prompt)
        kept = []
        removed = []

        # 从最新到最旧遍历，优先保留最近帧
        for frame in reversed(self.frames):
            frame_tokens = self._estimate_tokens(str(frame.content))
            if frame.frame_type == "task":
                kept.append(frame)
                current_tokens += frame_tokens
            elif current_tokens + frame_tokens <= max_tokens:
                kept.append(frame)
                current_tokens += frame_tokens
            else:
                removed.append(frame)

        if removed:
            self.frames = list(reversed(kept))
            logger.debug(
                "AgentContext 截断",
                agent_id=self.agent_id,
                removed=len(removed),
                kept=len(self.frames),
                tokens=current_tokens,
            )

    def archive(self) -> Dict[str, Any]:
        """归档当前上下文"""
        return {
            "agent_id": self.agent_id,
            "conversation_id": self.conversation_id,
            "frames": [
                {"type": f.frame_type, "content": f.content, "timestamp": f.timestamp.isoformat()}
                for f in self.frames
            ],
            "archived_at": datetime.utcnow().isoformat(),
        }

    def clear(self):
        """清理动态内容（节省 Token）"""
        self.frames = []


class AgentContextManager:
    """Agent Context 管理器"""

    def __init__(self):
        self._contexts: Dict[str, AgentContext] = {}

    def _key(self, agent_id: str, conversation_id: str) -> str:
        return f"{agent_id}:{conversation_id}"

    def initialize(
        self, agent_id: str, conversation_id: str, system_prompt: str = "", role_config: Dict = None
    ):
        """初始化 Agent 上下文"""
        key = self._key(agent_id, conversation_id)
        ctx = AgentContext(agent_id=agent_id, conversation_id=conversation_id)
        ctx.system_prompt = system_prompt
        ctx.role_config = role_config or {}
        self._contexts[key] = ctx

    def get(self, agent_id: str, conversation_id: str) -> AgentContext:
        """获取 Agent 上下文"""
        key = self._key(agent_id, conversation_id)
        if key not in self._contexts:
            self.initialize(agent_id, conversation_id)
        return self._contexts[key]

    def after_round(
        self, agent_id: str, conversation_id: str, archive: bool = False
    ) -> Optional[Dict]:
        """轮次结束后处理"""
        ctx = self.get(agent_id, conversation_id)
        result = None
        if archive:
            result = ctx.archive()
        ctx.clear()
        return result
