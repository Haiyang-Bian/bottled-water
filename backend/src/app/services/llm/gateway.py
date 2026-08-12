"""
[LEGACY] 模型网关，用于按 ModelConfig 流式/非流式调用外部 LLM。

.. deprecated::
    该模块已被 `model_provider` 替代，仅保留用于兼容旧代码。
    新代码请使用 model_provider 模块统一接口。
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session, selectinload

from app.core.config import get_settings
from app.core.errors import NotFoundError, ValidationAppError
from db.models import ModelConfig
from app.services.llm.ark import LLMResult, LLMStreamEvent
from app.services.llm.tool_calls import select_mock_tool_call
from app.services.model_config_resolver import build_model_provider_config
from model_provider import create_provider
from model_provider.core.interfaces import ChatMessage
from model_provider.core.streaming import collect_chat_stream


async def stream_model_config_chat(
    db: Session,
    model_config_id: str,
    messages: list[dict[str, Any]],
    *,
    tools: list[dict[str, Any]] | None = None,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncIterator[LLMStreamEvent]:
    """Stream an OpenAI-compatible model config with optional function tools."""

    model = db.get(ModelConfig, model_config_id)
    if not model or model.deleted_at is not None:
        raise NotFoundError("Model config not found")
    provider = model.provider
    if provider.status != "active":
        raise ValidationAppError("Model provider is not active")

    settings = get_settings()
    api_key = provider.api_key_ref
    if api_key == "env:ARK_API_KEY":
        api_key = settings.ark_api_key or os.getenv("ARK_API_KEY")
    if not api_key and provider.base_url.rstrip("/") == settings.ark_base_url.rstrip("/"):
        api_key = settings.ark_api_key or os.getenv("ARK_API_KEY")

    if api_key == "mock":
        user_text = next(
            (
                str(message.get("content") or "")
                for message in reversed(messages)
                if message.get("role") == "user"
            ),
            "",
        )
        mock_tool_call = select_mock_tool_call(messages, tools)
        if mock_tool_call:
            yield LLMStreamEvent(
                type="delta",
                text="我将调用已授权工具处理请求。",
                model=model.model_id,
            )
            yield LLMStreamEvent(
                type="tool_calls",
                tool_calls=[mock_tool_call],
                model=model.model_id,
            )
            yield LLMStreamEvent(type="done", usage={}, model=model.model_id)
            return
        text = f"[mock-openai-compatible] {model.name} 已接收提示：{user_text[:120]}"
        for token in text.split(" "):
            yield LLMStreamEvent(type="delta", text=token + " ", model=model.model_id)
            await asyncio.sleep(0.025)
        yield LLMStreamEvent(type="done", usage={}, model=model.model_id)
        return

    if not api_key:
        raise ValidationAppError("Model provider API key is missing; set LLM_PROVIDER=mock for offline demos")

    mp = create_provider(build_model_provider_config(provider, model, str(api_key)))
    chat_messages = [
        ChatMessage(
            role=str(message.get("role") or "user"),
            content=str(message.get("content") or ""),
            name=message.get("name"),
            tool_calls=message.get("tool_calls"),
            tool_call_id=message.get("tool_call_id"),
            reasoning_content=message.get("reasoning_content"),
        )
        for message in messages
    ]
    accumulated_tool_calls: dict[int, dict[str, Any]] = {}
    async for chunk in mp.chat_stream(
        messages=chat_messages,
        tools=tools,
        temperature=model.temperature_default if temperature is None else temperature,
        max_tokens=min(max_tokens or model.max_output_tokens, model.max_output_tokens, 4096),
    ):
        if chunk.content or chunk.reasoning:
            yield LLMStreamEvent(
                type="delta",
                text=chunk.content,
                reasoning=chunk.reasoning,
                model=model.model_id,
            )
        if chunk.tool_call:
            index = int(chunk.tool_call.get("index", 0) or 0)
            _merge_stream_tool_call(
                accumulated_tool_calls.setdefault(index, {}), chunk.tool_call
            )
        if chunk.finish_reason == "tool_calls" and accumulated_tool_calls:
            yield LLMStreamEvent(
                type="tool_calls",
                tool_calls=_final_tool_calls(accumulated_tool_calls),
                model=model.model_id,
            )
            accumulated_tool_calls = {}
    if accumulated_tool_calls:
        yield LLMStreamEvent(
            type="tool_calls",
            tool_calls=_final_tool_calls(accumulated_tool_calls),
            model=model.model_id,
        )
    yield LLMStreamEvent(type="done", usage={}, model=model.model_id)


def _merge_stream_tool_call(target: dict[str, Any], source: dict[str, Any]) -> None:
    if source.get("id"):
        target["id"] = source["id"]
    if source.get("type"):
        target["type"] = source["type"]
    function = source.get("function")
    if isinstance(function, dict):
        target_function = target.setdefault("function", {})
        if function.get("name"):
            target_function["name"] = function["name"]
        if "arguments" in function:
            target_function["arguments"] = str(
                target_function.get("arguments") or ""
            ) + str(function.get("arguments") or "")


def _final_tool_calls(values: dict[int, dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "id": value.get("id", ""),
            "type": value.get("type", "function"),
            "function": value.get("function", {}),
        }
        for _, value in sorted(values.items())
        if value.get("function", {}).get("name")
    ]


def _mock_result(model: ModelConfig, prompt: str, reason: str = "mock") -> LLMResult:
    text = f"[mock-openai-compatible] {model.name} 已接收测试提示：{prompt[:120]}"
    return LLMResult(
        text=text,
        model=model.model_id,
        usage={"input_tokens": len(prompt) // 2, "output_tokens": 32},
        raw={"mock": True, "reason": reason},
    )


async def test_model_config(db: AsyncSession, model_config_id: str, prompt: str) -> LLMResult:
    """测试模型配置（非流式）。"""
    from app.services.model_config_resolver import resolve_api_key
    from model_provider import create_provider

    model = await db.scalar(
        select(ModelConfig)
        .options(selectinload(ModelConfig.provider))
        .where(ModelConfig.id == model_config_id, ModelConfig.deleted_at.is_(None))
    )
    if not model:
        raise NotFoundError("模型配置不存在")
    provider = model.provider
    if provider.status != "active":
        raise ValidationAppError("模型供应商未启用")

    api_key = await resolve_api_key(provider, model)

    if api_key == "mock":
        return _mock_result(model, prompt)
    if not api_key:
        raise ValidationAppError("模型供应商缺少 API Key；如需离线演示请显式设置 LLM_PROVIDER=mock")

    mp = create_provider(build_model_provider_config(provider, model, api_key))

    try:
        response = await collect_chat_stream(
            mp,
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=model.temperature_default,
            max_tokens=min(model.max_output_tokens, 1024),
        )
    except Exception as exc:
        raise ValidationAppError(f"模型真实连接失败：{exc.__class__.__name__}: {exc}") from exc

    return LLMResult(
        text=response.content or "",
        model=model.model_id,
        usage=response.usage or {},
        raw={"content": response.content, "model": response.model},
    )


async def stream_model_config(
    db: AsyncSession,
    model_config_id: str,
    prompt: str,
) -> AsyncIterator[dict[str, Any]]:
    """流式调用模型配置，逐 token 返回生成结果。

    Args:
        db: 数据库会话。
        model_config_id: 模型配置 ID。
        prompt: 提示词。

    Yields:
        包含生成文本片段的字典，如 {"text": "Hello"}。
    """
    from app.services.model_config_resolver import resolve_api_key
    from model_provider import create_provider

    model = await db.scalar(
        select(ModelConfig)
        .options(selectinload(ModelConfig.provider))
        .where(ModelConfig.id == model_config_id, ModelConfig.deleted_at.is_(None))
    )
    if not model:
        raise NotFoundError("模型配置不存在")
    provider = model.provider
    if provider.status != "active":
        raise ValidationAppError("模型供应商未启用")

    api_key = await resolve_api_key(provider, model)

    if api_key == "mock":
        text = f"[mock-openai-compatible] {model.name} 已接收测试提示：{prompt[:120]}"
        for token in text.split(" "):
            yield {"text": token + " "}
            await asyncio.sleep(0.025)
        return

    if not api_key:
        raise ValidationAppError("模型供应商缺少 API Key；如需离线演示请显式设置 LLM_PROVIDER=mock")

    mp = create_provider(build_model_provider_config(provider, model, api_key))

    try:
        async for chunk in mp.chat_stream(
            messages=[ChatMessage(role="user", content=prompt)],
            temperature=model.temperature_default,
            max_tokens=min(model.max_output_tokens, 1024),
        ):
            if chunk.content:
                yield {"text": chunk.content}
    except Exception as exc:
        raise ValidationAppError(f"模型流式调用失败：{exc.__class__.__name__}: {exc}") from exc
