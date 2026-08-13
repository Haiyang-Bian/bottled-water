from __future__ import annotations

from typing import Any, List, Optional

from .interfaces import BaseModelProvider, ChatMessage, ChatResponse


async def collect_chat_stream(
    provider: BaseModelProvider,
    *,
    messages: List[ChatMessage],
    system_prompt: Optional[str] = None,
    tools: Optional[List[dict[str, Any]]] = None,
    temperature: float = 0.7,
    max_tokens: Optional[int] = None,
) -> ChatResponse:
    """Consume a streaming model response without emitting UI events.

    Internal callers such as schedulers need the complete response for JSON
    parsing, but they should still use the same streaming transport as visible
    chat turns. This helper keeps that boundary explicit.
    """

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    tool_calls: dict[int, dict[str, Any]] = {}

    async for chunk in provider.chat_stream(
        messages=messages,
        system_prompt=system_prompt,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
    ):
        if chunk.content:
            content_parts.append(chunk.content)
        if chunk.reasoning:
            reasoning_parts.append(chunk.reasoning)
        if chunk.tool_call:
            index = _tool_call_index(tool_calls, chunk.tool_call)
            existing = tool_calls.setdefault(index, {})
            _merge_tool_call(existing, chunk.tool_call)

    return ChatResponse(
        content="".join(content_parts),
        tool_calls=[tool_calls[i] for i in sorted(tool_calls)] if tool_calls else None,
        usage=None,
        model=getattr(provider, "model", None),
        reasoning_content="".join(reasoning_parts),
    )


def _merge_tool_call(target: dict[str, Any], source: dict[str, Any]) -> None:
    if "id" in source and source["id"]:
        target["id"] = source["id"]
    if "type" in source and source["type"]:
        target["type"] = source["type"]
    function = source.get("function")
    if not isinstance(function, dict):
        return
    target_function = target.setdefault("function", {})
    if function.get("name"):
        target_function["name"] = function["name"]
    if "arguments" in function:
        target_function["arguments"] = str(target_function.get("arguments") or "") + str(
            function.get("arguments") or ""
        )


def _tool_call_index(existing: dict[int, dict[str, Any]], source: dict[str, Any]) -> int:
    raw_index = source.get("index")
    if raw_index is not None:
        return int(raw_index)

    source_id = str(source.get("id") or "")
    if source_id:
        for index, item in existing.items():
            if str(item.get("id") or "") == source_id:
                return index

    function = source.get("function") if isinstance(source.get("function"), dict) else {}
    source_name = str(function.get("name") or "")
    if source_name:
        for index, item in existing.items():
            item_function = item.get("function") if isinstance(item.get("function"), dict) else {}
            if str(item_function.get("name") or "") == source_name:
                return index

    if len(existing) == 1:
        return next(iter(existing))

    return max(existing.keys(), default=-1) + 1 if existing else 0
