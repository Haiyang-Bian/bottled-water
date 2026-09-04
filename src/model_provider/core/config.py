"""
统一配置格式

所有模型都用同一套配置结构，底层适配到各家 SDK 的参数。
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, Any


@dataclass
class ModelConfig:
    """统一模型配置

    示例：
        # 火山引擎
        ModelConfig(
            provider="ark",
            model="ep-xxx",
            api_key="ak-xxx",
        )

        # OpenAI
        ModelConfig(
            provider="openai",
            model="gpt-4",
            api_key="sk-xxx",
            base_url=None,  # 可选自定义地址
        )

        # Anthropic
        ModelConfig(
            provider="anthropic",
            model="claude-3-opus",
            api_key="sk-ant-xxx",
        )
    """

    provider: str              # "ark" | "openai" | "anthropic" | ...
    model: str                 # 模型 ID
    api_key: str               # API 密钥
    base_url: Optional[str] = None  # 自定义地址（可选）

    # 通用参数（各 Provider 自行映射到 SDK 参数）
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None

    # 扩展参数（透传给对应 SDK）
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """转字典（用于工厂创建）"""
        return {
            "provider": self.provider,
            "model": self.model,
            "api_key": self.api_key,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "top_p": self.top_p,
            **self.extra,
        }
