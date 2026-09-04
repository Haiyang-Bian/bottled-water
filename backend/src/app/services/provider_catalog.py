"""Web provider form metadata; not part of the execution system."""

from typing import Dict

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


def get_builtin_providers() -> list[dict]:
    """获取内置 Provider 列表（前端厂商下拉菜单数据源）。

    Returns:
        每个元素包含 provider_type、name、base_url、default_model 等字段的字典列表。
    """
    result = []
    for provider_type, meta in _PROVIDER_METADATA.items():
        result.append({"provider_type": provider_type, **meta})
    return result
