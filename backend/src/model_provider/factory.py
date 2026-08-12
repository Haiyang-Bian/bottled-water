"""
模型提供者工厂

根据统一配置创建对应的模型提供者实例。
核心功能：一行代码切换模型，业务层无感知。
"""

from typing import Dict, Any, Union

from common.logger import get_logger
from .core.interfaces import BaseModelProvider
from .core.config import ModelConfig
from .providers.ark import ArkProvider
from .providers.deepseek import DeepSeekProvider
from .providers.openai_compatible import OpenAICompatibleProvider

logger = get_logger(__name__)


_PROVIDER_REGISTRY: Dict[str, type] = {
    "ark": ArkProvider,
    "volcengine": ArkProvider,
    "openai_compatible": OpenAICompatibleProvider,
    "openai": OpenAICompatibleProvider,
    "deepseek": DeepSeekProvider,
}

# 内置 Provider 元数据（前端厂商列表来源）
_PROVIDER_METADATA: Dict[str, dict] = {
    "volcengine": {
        "name": "火山引擎",
        "base_url": "https://ark.cn-beijing.volces.com/api/v3",
        "default_model": "doubao-seed-2-0-lite",
        "supports_streaming": True,
        "supports_embeddings": False,
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o",
        "supports_streaming": True,
        "supports_embeddings": True,
    },
    "openai_compatible": {
        "name": "OpenAI 兼容",
        "base_url": "",
        "default_model": "",
        "supports_streaming": True,
        "supports_embeddings": False,
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com",
        "default_model": "deepseek-v4-flash",
        "models": [
            {"id": "deepseek-v4-flash", "name": "DeepSeek V4 Flash"},
            {"id": "deepseek-v4-pro", "name": "DeepSeek V4 Pro"},
        ],
        "supports_streaming": True,
        "supports_embeddings": False,
        "supports_tools": True,
        "supports_thinking": True,
        "reasoning_efforts": ["high", "max"],
    },
}


def create_provider(config: Union[ModelConfig, Dict[str, Any]]) -> BaseModelProvider:
    """根据配置创建模型提供者

    Args:
        config: ModelConfig 对象或字典

    Returns:
        BaseModelProvider 实例

    Raises:
        ValueError: 未知的 provider 类型
    """
    if isinstance(config, ModelConfig):
        config = config.to_dict()

    provider_type = config.get("provider", "ark").lower()

    provider_cls = _PROVIDER_REGISTRY.get(provider_type)
    if not provider_cls:
        logger.error("创建 Provider 失败", provider_type=provider_type)
        raise ValueError(
            f"Unknown provider: '{provider_type}'. "
            f"Available: {list(_PROVIDER_REGISTRY.keys())}"
        )

    model = config.get("model", "unknown")
    logger.info("Provider 创建成功", provider=provider_type, model=model)
    return provider_cls(config)


def list_providers() -> list[str]:
    """列出可用的提供者类型"""
    return list(_PROVIDER_REGISTRY.keys())


def register_provider(name: str, provider_cls: type):
    """注册自定义提供者（插件扩展用）

    示例：
        from model_provider import register_provider
        register_provider("my_provider", MyProvider)
    """
    if not issubclass(provider_cls, BaseModelProvider):
        raise TypeError("Provider must inherit BaseModelProvider")
    _PROVIDER_REGISTRY[name] = provider_cls


def get_provider_info() -> Dict[str, str]:
    """获取所有已注册 Provider 的信息"""
    return {
        name: cls.__doc__ or name
        for name, cls in _PROVIDER_REGISTRY.items()
    }


def get_builtin_providers() -> list[dict]:
    """获取内置 Provider 列表（前端厂商下拉菜单数据源）。

    Returns:
        每个元素包含 provider_type、name、base_url、default_model 等字段的字典列表。
    """
    result = []
    for provider_type, meta in _PROVIDER_METADATA.items():
        result.append({"provider_type": provider_type, **meta})
    return result
