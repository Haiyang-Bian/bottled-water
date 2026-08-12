"""DeepSeek V4 provider using the official OpenAI-compatible API."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..core.interfaces import ChatMessage
from .openai_compatible import OpenAICompatibleProvider


class DeepSeekProvider(OpenAICompatibleProvider):
    """DeepSeek V4 Flash/Pro with optional thinking mode."""

    def __init__(self, config: Dict[str, Any]):
        value = {**config, "base_url": config.get("base_url") or "https://api.deepseek.com"}
        super().__init__(value)
        self.thinking_enabled = bool(config.get("thinking_enabled", False))
        self.reasoning_effort = str(config.get("reasoning_effort") or "high")
        if self.reasoning_effort not in {"high", "max"}:
            raise ValueError("DeepSeek reasoning_effort must be 'high' or 'max'")

    def _build_payload(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str],
        tools: Optional[List[Dict]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> dict[str, Any]:
        payload = super()._build_payload(
            messages, system_prompt, tools, temperature, max_tokens
        )
        payload["extra_body"] = {
            "thinking": {"type": "enabled" if self.thinking_enabled else "disabled"}
        }
        if self.thinking_enabled:
            payload.pop("temperature", None)
            payload.pop("top_p", None)
            payload["reasoning_effort"] = self.reasoning_effort
        return payload
