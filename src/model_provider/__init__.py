"""
model_provider - 统一模型接口

为上层提供单一、统一的 LLM 调用接口，底层自动适配各家 SDK。

使用示例：
    from model_provider import create_provider, ModelConfig

    # 创建模型实例（底层自动选择对应 SDK）
    provider = create_provider(ModelConfig(
        provider="ark",
        model="ep-xxx",
        api_key="ak-xxx",
    ))

    # 统一调用，无需关心底层是哪家
    response = await provider.chat(
        messages=[ChatMessage(role="user", content="你好")],
        system_prompt="你是助手",
    )

    # 流式调用
    async for chunk in provider.chat_stream(messages=...):
        print(chunk.content, end="")
"""

from .core.interfaces import BaseModelProvider, ChatMessage, ChatResponse, StreamChunk
from .core.config import ModelConfig
from .factory import (
    create_provider,
    list_providers,
    register_provider,
    get_provider_info,
)

__all__ = [
    # 接口
    "BaseModelProvider",
    "ChatMessage",
    "ChatResponse",
    "StreamChunk",
    # 配置
    "ModelConfig",
    # 工厂
    "create_provider",
    "list_providers",
    "register_provider",
    "get_provider_info",
]
