"""OpenAI-compatible model provider.

Non-streaming calls still use the Ark SDK for compatibility with the existing
code path. Streaming calls intentionally use raw HTTP chunk consumption because
the SDK can return an iterator only after the upstream response has already
been buffered, which makes the app-level WebSocket stream appear non-streaming.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any, Dict, List, Optional

import httpx
from volcenginesdkarkruntime import AsyncArk

from common.logger import get_logger

from ..core.interfaces import BaseModelProvider, ChatMessage, ChatResponse, StreamChunk

logger = get_logger(__name__)


DEFAULT_BASE_URLS = {
    "ark": "https://ark.cn-beijing.volces.com/api/v3",
    "volcengine": "https://ark.cn-beijing.volces.com/api/v3",
    "openai": "https://api.openai.com/v1",
}


class ArkProvider(BaseModelProvider):
    """OpenAI-compatible provider used by Ark, OpenAI, and custom endpoints."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.provider = str(config.get("provider") or "ark").lower()
        self.api_key = config["api_key"]
        self.base_url = str(
            config.get("base_url") or DEFAULT_BASE_URLS.get(self.provider) or ""
        ).rstrip("/")
        self.stream_timeout_seconds = float(
            config.get("stream_timeout_seconds") or config.get("timeout_seconds") or 120
        )

        client_kwargs = {"api_key": self.api_key}
        if config.get("base_url"):
            client_kwargs["base_url"] = config["base_url"]

        self.client = AsyncArk(**client_kwargs)

    async def chat(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> ChatResponse:
        payload = self._build_payload(messages, system_prompt, tools, temperature, max_tokens)

        logger.info("chat call started", model=self.model, msg_count=len(messages))

        try:
            response = await self.client.chat.completions.create(**payload)
        except Exception as e:
            logger.error(f"chat call failed model={self.model} error={str(e)}")
            raise

        choice = response.choices[0]
        message = choice.message

        tool_calls = None
        if hasattr(message, "tool_calls") and message.tool_calls:
            tool_calls = []
            for tc in message.tool_calls:
                tc_dict = {"id": tc.id, "type": tc.type}
                if hasattr(tc, "function"):
                    tc_dict["function"] = {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    }
                tool_calls.append(tc_dict)

        usage = None
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }
            logger.info("chat call completed", model=self.model, usage=usage)
        else:
            logger.info("chat call completed", model=self.model)

        return ChatResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            usage=usage,
            model=response.model if hasattr(response, "model") else None,
            reasoning_content=str(getattr(message, "reasoning_content", "") or ""),
            finish_reason=getattr(choice, "finish_reason", None),
        )

    async def chat_stream(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str] = None,
        tools: Optional[List[Dict]] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[StreamChunk]:
        payload = self._build_payload(messages, system_prompt, tools, temperature, max_tokens)
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}

        logger.info("chat_stream call started", model=self.model, msg_count=len(messages))

        try:
            async for data in self._stream_chat_completion_payloads(payload):
                for chunk in _stream_chunks_from_payload(data):
                    yield chunk
        except Exception as e:
            logger.error(f"chat_stream call failed model={self.model} error={str(e)}")
            raise

    async def _stream_chat_completion_payloads(
        self, payload: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        if not self.base_url:
            raise ValueError("base_url is required for streaming chat completions")

        timeout = httpx.Timeout(self.stream_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
            ) as response:
                if response.status_code < 200 or response.status_code >= 300:
                    body = (await response.aread()).decode("utf-8", "replace")
                    raise RuntimeError(
                        f"streaming chat completion failed: "
                        f"status={response.status_code} body={body}"
                    )

                content_type = getattr(response, "headers", {}).get("content-type", "")
                if content_type and "text/event-stream" not in content_type.lower():
                    body = (await response.aread()).decode("utf-8", "replace")
                    try:
                        yield json.loads(body)
                    except json.JSONDecodeError:
                        logger.warning("skip malformed chat completion payload", payload=body[:200])
                    return

                async for line in response.aiter_lines():
                    data_text = _sse_data_text(line)
                    if data_text is None:
                        continue
                    if data_text == "[DONE]":
                        break
                    try:
                        yield json.loads(data_text)
                    except json.JSONDecodeError:
                        logger.warning("skip malformed streaming payload", payload=data_text[:200])

    async def list_models(self) -> list[dict]:
        """List available models for the configured provider."""
        try:
            models_data = await self.client.models.list()
            return [
                {"id": m.id, "name": getattr(m, "name", m.id), "status": "active"}
                for m in models_data.data
            ]
        except Exception as e:
            logger.warning(f"list_models call failed: {e}")
            return []

    def _build_payload(
        self,
        messages: List[ChatMessage],
        system_prompt: Optional[str],
        tools: Optional[List[Dict]],
        temperature: float,
        max_tokens: Optional[int],
    ) -> Dict[str, Any]:
        payload_messages = []
        if system_prompt:
            payload_messages.append({"role": "system", "content": system_prompt})
        for message in messages:
            payload_message = {"role": message.role, "content": message.content}
            if message.name:
                payload_message["name"] = message.name
            if message.tool_calls:
                payload_message["tool_calls"] = message.tool_calls
            if message.reasoning_content:
                payload_message["reasoning_content"] = message.reasoning_content
            if message.role == "tool" and message.tool_call_id:
                payload_message["tool_call_id"] = message.tool_call_id
            payload_messages.append(payload_message)

        payload = {
            "model": self.model,
            "messages": payload_messages,
            "temperature": temperature,
        }
        if self._supports_thinking_control():
            payload["thinking"] = {"type": "disabled"}
        if tools:
            payload["tools"] = tools
        if max_tokens:
            payload["max_tokens"] = max_tokens
        return payload

    def _supports_thinking_control(self) -> bool:
        return self.provider in {"ark", "volcengine"} or "volces.com" in self.base_url


def _sse_data_text(line: str) -> str | None:
    line = line.strip()
    if not line or not line.startswith("data:"):
        return None
    return line.removeprefix("data:").strip()


def _stream_chunks_from_payload(data: dict[str, Any]) -> list[StreamChunk]:
    chunks: list[StreamChunk] = []
    for choice in data.get("choices") or []:
        if not isinstance(choice, dict):
            continue

        delta = choice.get("delta") or choice.get("message") or {}
        if not isinstance(delta, dict):
            delta = {}

        finish_reason = choice.get("finish_reason")
        tool_call_deltas = [
            tool_call
            for tool_call in delta.get("tool_calls") or []
            if isinstance(tool_call, dict)
        ]

        content = str(delta.get("content") or "")
        reasoning = str(delta.get("reasoning_content") or "")
        if content or reasoning:
            chunks.append(
                StreamChunk(
                    content=content,
                    reasoning=reasoning,
                    finish_reason=None if tool_call_deltas else finish_reason,
                )
            )

        for index, tool_call_delta in enumerate(tool_call_deltas):
            tool_call = _tool_call_delta_to_dict(tool_call_delta)
            if not tool_call:
                continue
            is_last_tool_call = index == len(tool_call_deltas) - 1
            chunks.append(
                StreamChunk(
                    tool_call=tool_call,
                    finish_reason=finish_reason if is_last_tool_call else None,
                )
            )

        if finish_reason and not (content or reasoning or tool_call_deltas):
            chunks.append(StreamChunk(finish_reason=finish_reason))

    return chunks


def _tool_call_delta_to_dict(tool_call_delta: dict[str, Any]) -> dict[str, Any]:
    tool_call: dict[str, Any] = {}

    if tool_call_delta.get("index") is not None:
        tool_call["index"] = int(tool_call_delta["index"])
    if tool_call_delta.get("id"):
        tool_call["id"] = tool_call_delta["id"]
    if tool_call_delta.get("type"):
        tool_call["type"] = tool_call_delta["type"]

    function = tool_call_delta.get("function")
    if isinstance(function, dict):
        function_delta: dict[str, Any] = {}
        if function.get("name") is not None:
            function_delta["name"] = function["name"]
        if function.get("arguments") is not None:
            function_delta["arguments"] = function["arguments"]
        if function_delta:
            tool_call["function"] = function_delta

    return tool_call
