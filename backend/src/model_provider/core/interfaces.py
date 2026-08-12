"""
模型提供者抽象接口

只定义统一调用接口，不关心底层 HTTP 细节。
各家 SDK 的适配在 providers/ 中实现。
"""

from abc import ABC, abstractmethod
from typing import AsyncIterator, List, Optional, Dict, Any
from dataclasses import dataclass


@dataclass
class ChatMessage:
    """统一消息格式"""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: Optional[str] = None
    tool_calls: Optional[List[Dict]] = None
    tool_call_id: Optional[str] = None
    reasoning_content: Optional[str] = None


@dataclass
class ChatResponse:
    """统一响应格式"""
    content: str
    tool_calls: Optional[List[Dict]] = None
    usage: Optional[Dict[str, int]] = None
    model: Optional[str] = None
    reasoning_content: str = ""
    finish_reason: Optional[str] = None


@dataclass
class StreamChunk:
    """流式响应块"""
    content: str = ""
    reasoning: str = ""
    tool_call: Optional[Dict] = None
    finish_reason: Optional[str] = None


class BaseModelProvider(ABC):
    """模型提供者基类

    子类直接使用对应厂商的 SDK，不需要自己封装 HTTP。
    例如：
        - ArkProvider 用 volcengine/ark 包
        - OpenAIProvider 用 openai 包
        - AnthropicProvider 用 anthropic 包
    """

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.model = config.get("model")

    @abstractmethod
    async def chat(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        """非流式对话"""
        pass

    @abstractmethod
    async def chat_stream(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        """流式对话"""
        pass

    def count_tokens(self, text: str) -> int:
        """Token 估算（默认实现，子类可覆盖）"""
        import re
        cn_chars = len(re.findall(r'[一-鿿]', text))
        en_words = len(re.findall(r'[a-zA-Z]+', text))
        return cn_chars + int(en_words * 1.3) + 10

    async def list_models(self) -> list[dict]:
        """列出服务商下所有可用模型（子类实现）"""
        return []
