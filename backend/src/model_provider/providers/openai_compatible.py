"""OpenAI SDK based provider for OpenAI-compatible Chat Completions APIs."""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from collections.abc import AsyncIterator
from typing import Any, Dict, List, Optional

from openai import AsyncOpenAI

from common.logger import get_logger

from ..core.interfaces import BaseModelProvider, ChatMessage, ChatResponse, StreamChunk

logger = get_logger(__name__)


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
}

_VALID_TOOL_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class OpenAICompatibleProvider(BaseModelProvider):
    """Provider backed by the official ``openai`` Python package."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider = str(config.get("provider") or "openai_compatible").lower()
        self.api_key = str(config.get("api_key") or "")
        self.base_url = str(
            config.get("base_url") or DEFAULT_BASE_URLS.get(self.provider) or ""
        ).rstrip("/")
        client_options: dict[str, Any] = {"api_key": self.api_key}
        if self.base_url:
            client_options["base_url"] = self.base_url
        if config.get("timeout_seconds"):
            client_options["timeout"] = float(config["timeout_seconds"])
        self.client = AsyncOpenAI(**client_options)

    async def chat(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        provider_tools, tool_aliases = _alias_tool_definitions(tools)
        provider_messages = _alias_message_tool_calls(messages, tool_aliases)
        payload = self._build_payload(
            provider_messages,
            system_prompt,
            provider_tools,
            temperature,
            max_tokens,
        )
        response = await self.client.chat.completions.create(**payload)
        choice = response.choices[0]
        message = choice.message
        tool_calls = [
            _restore_tool_call_name(item.model_dump(exclude_none=True), tool_aliases)
            for item in (message.tool_calls or [])
        ] or None
        usage = response.usage.model_dump() if response.usage else None
        return ChatResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            model=response.model,
            reasoning_content=_reasoning_content(message),
            finish_reason=choice.finish_reason,
        )

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        provider_tools, tool_aliases = _alias_tool_definitions(tools)
        provider_messages = _alias_message_tool_calls(messages, tool_aliases)
        payload = self._build_payload(
            provider_messages,
            system_prompt,
            provider_tools,
            temperature,
            max_tokens,
        )
        stream = await self.client.chat.completions.create(
            **payload,
            stream=True,
            stream_options={"include_usage": True},
        )
        async for response_chunk in stream:
            for choice in response_chunk.choices:
                delta = choice.delta
                reasoning = _reasoning_content(delta)
                if delta.content or reasoning:
                    yield StreamChunk(
                        content=delta.content or "",
                        reasoning=reasoning,
                        finish_reason=None if delta.tool_calls else choice.finish_reason,
                    )
                tool_call_deltas = delta.tool_calls or []
                for index, tool_call in enumerate(tool_call_deltas):
                    yield StreamChunk(
                        tool_call=_restore_tool_call_name(
                            tool_call.model_dump(exclude_none=True),
                            tool_aliases,
                        ),
                        finish_reason=(
                            choice.finish_reason
                            if index == len(tool_call_deltas) - 1
                            else None
                        ),
                    )
                if choice.finish_reason and not (
                    delta.content or reasoning or delta.tool_calls
                ):
                    yield StreamChunk(finish_reason=choice.finish_reason)

    async def list_models(self) -> list[dict]:
        try:
            models = await self.client.models.list()
            return [
                {"id": item.id, "name": item.id, "status": "active"}
                for item in models.data
            ]
        except Exception as exc:
            logger.warning("list_models call failed", provider=self.provider, error=str(exc))
            return []

    def _build_payload(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str],
        tools: Optional[List[Dict]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": self._build_messages(messages, system_prompt),
            "temperature": temperature,
        }
        if self.config.get("top_p") is not None:
            payload["top_p"] = self.config["top_p"]
        if max_tokens:
            payload["max_tokens"] = max_tokens
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        return payload

    @staticmethod
    def _build_messages(
        messages: List[ChatMessage], system_prompt: Optional[str]
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        if system_prompt:
            result.append({"role": "system", "content": system_prompt})
        for message in messages:
            value: dict[str, Any] = {"role": message.role, "content": message.content}
            if message.name:
                value["name"] = message.name
            if message.tool_calls:
                value["tool_calls"] = message.tool_calls
            if message.role == "tool" and message.tool_call_id:
                value["tool_call_id"] = message.tool_call_id
            if message.role == "assistant" and message.reasoning_content:
                value["reasoning_content"] = message.reasoning_content
            result.append(value)
        return result


def _reasoning_content(value: Any) -> str:
    direct = getattr(value, "reasoning_content", None)
    if direct:
        return str(direct)
    extra = getattr(value, "model_extra", None)
    if isinstance(extra, dict) and extra.get("reasoning_content"):
        return str(extra["reasoning_content"])
    return ""


def _alias_tool_definitions(
    tools: Optional[List[Dict]],
) -> tuple[Optional[List[Dict]], dict[str, str]]:
    """Make internal tool names OpenAI-compatible without mutating callers.

    AgentHub uses dotted names such as ``file.read`` for routing. OpenAI-compatible
    APIs only accept letters, digits, underscores, and dashes in function names.
    The alias map restores provider responses to the internal name before they
    reach the Runtime tool executor.
    """
    if not tools:
        return tools, {}

    valid_names = {
        str(item.get("function", {}).get("name") or "")
        for item in tools
        if _VALID_TOOL_NAME.fullmatch(
            str(item.get("function", {}).get("name") or "")
        )
    }
    aliases_by_original: dict[str, str] = {}
    originals_by_alias: dict[str, str] = {}
    aliased_tools: List[Dict] = []

    for item in tools:
        aliased_item = deepcopy(item)
        function = aliased_item.get("function")
        if not isinstance(function, dict):
            aliased_tools.append(aliased_item)
            continue

        original_name = str(function.get("name") or "")
        if _VALID_TOOL_NAME.fullmatch(original_name):
            alias = original_name
        else:
            alias = aliases_by_original.get(original_name) or _make_tool_alias(
                original_name,
                reserved=valid_names | set(originals_by_alias),
            )
            aliases_by_original[original_name] = alias
            originals_by_alias[alias] = original_name
        function["name"] = alias
        aliased_tools.append(aliased_item)

    return aliased_tools, originals_by_alias


def _make_tool_alias(name: str, *, reserved: set[str]) -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_-") or "tool"
    for digest_length in range(8, 33, 4):
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:digest_length]
        max_stem_length = 64 - digest_length - 1
        alias = f"{stem[:max_stem_length]}_{digest}"
        if alias not in reserved:
            return alias
    raise ValueError("Unable to create a unique provider tool alias")


def _restore_tool_call_name(
    tool_call: dict[str, Any],
    aliases: dict[str, str],
) -> dict[str, Any]:
    function = tool_call.get("function")
    if not isinstance(function, dict):
        return tool_call
    alias = str(function.get("name") or "")
    original = aliases.get(alias)
    if original:
        function["name"] = original
    return tool_call


def _alias_message_tool_calls(
    messages: List[ChatMessage],
    aliases: dict[str, str],
) -> List[ChatMessage]:
    if not aliases:
        return messages
    aliases_by_original = {original: alias for alias, original in aliases.items()}
    result: List[ChatMessage] = []
    for message in messages:
        tool_calls = deepcopy(message.tool_calls)
        for tool_call in tool_calls or []:
            function = tool_call.get("function")
            if not isinstance(function, dict):
                continue
            original = str(function.get("name") or "")
            if original in aliases_by_original:
                function["name"] = aliases_by_original[original]
        name = aliases_by_original.get(message.name or "", message.name)
        result.append(
            ChatMessage(
                role=message.role,
                content=message.content,
                name=name,
                tool_calls=tool_calls,
                tool_call_id=message.tool_call_id,
                reasoning_content=message.reasoning_content,
            )
        )
    return result
