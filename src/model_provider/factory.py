"""
模型提供者工厂

根据统一配置创建对应的模型提供者实例。
核心功能：一行代码切换模型，业务层无感知。
"""

from typing import Dict, Any, Union

from agent_contracts.logging import get_logger
from .core.interfaces import BaseModelProvider
from .core.config import ModelConfig
from importlib import import_module

logger = get_logger(__name__)


_PROVIDER_REGISTRY: Dict[str, type | tuple[str, str]] = {
    "ark": ("ark", "ArkProvider"),
    "volcengine": ("ark", "ArkProvider"),
    "openai_compatible": ("openai_compatible", "OpenAICompatibleProvider"),
    "openai": ("openai_compatible", "OpenAICompatibleProvider"),
    "deepseek": ("deepseek", "DeepSeekProvider"),
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

    if isinstance(provider_cls, tuple):
        module, name = provider_cls
        provider_cls = getattr(import_module(f"model_provider.providers.{module}"), name)

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
        name: (cls[1] if isinstance(cls, tuple) else cls.__doc__) or name
        for name, cls in _PROVIDER_REGISTRY.items()
    }


