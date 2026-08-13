from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from model_provider import ChatMessage, ModelConfig, create_provider, get_builtin_providers
from model_provider.providers import deepseek as deepseek_module
from model_provider.providers import openai_compatible as openai_module


pytestmark = [pytest.mark.unit, pytest.mark.providers]


class FakeToolCall:
    def __init__(self, value: dict[str, Any]):
        self.value = value

    def model_dump(self, **_kwargs):
        return self.value


class FakeAsyncStream:
    def __init__(self, values: list[Any]):
        self.values = values

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for value in self.values:
            yield value


class FakeCompletions:
    def __init__(self):
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        requested_tool_name = (
            kwargs.get("tools", [{}])[0].get("function", {}).get("name", "lookup")
            if kwargs.get("tools")
            else "lookup"
        )
        if kwargs.get("stream"):
            return FakeAsyncStream(
                [
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[],
                                    model_extra={"reasoning_content": "plan"},
                                ),
                                finish_reason=None,
                            )
                        ]
                    ),
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        FakeToolCall(
                                            {
                                                "index": 0,
                                                "id": "call-1",
                                                "type": "function",
                                                "function": {
                                                    "name": requested_tool_name,
                                                    "arguments": '{"q":',
                                                },
                                            }
                                        )
                                    ],
                                    model_extra={},
                                ),
                                finish_reason=None,
                            )
                        ]
                    ),
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        FakeToolCall(
                                            {
                                                "index": 0,
                                                "function": {"arguments": '"water"}'},
                                            }
                                        )
                                    ],
                                    model_extra={},
                                ),
                                finish_reason="tool_calls",
                            )
                        ]
                    ),
                ]
            )
        tool_calls = []
        if kwargs.get("tools"):
            tool_calls = [
                FakeToolCall(
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": requested_tool_name,
                            "arguments": "{}",
                        },
                    }
                )
            ]
        message = SimpleNamespace(
            content="answer",
            tool_calls=tool_calls,
            model_extra={"reasoning_content": "reason"},
        )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=message, finish_reason="stop")],
            usage=None,
            model=kwargs["model"],
        )


class FakeModels:
    async def list(self):
        return SimpleNamespace(
            data=[
                SimpleNamespace(id="deepseek-v4-flash"),
                SimpleNamespace(id="deepseek-v4-pro"),
            ]
        )


class FakeClient:
    def __init__(self, **kwargs):
        self.options = kwargs
        self.chat = SimpleNamespace(completions=FakeCompletions())
        self.models = FakeModels()


@pytest.fixture
def fake_client(monkeypatch):
    clients: list[FakeClient] = []

    def factory(**kwargs):
        client = FakeClient(**kwargs)
        clients.append(client)
        return client

    monkeypatch.setattr(openai_module, "AsyncOpenAI", factory)
    return clients


def make_provider(fake_client, **extra):
    return deepseek_module.DeepSeekProvider(
        {
            "provider": "deepseek",
            "model": "deepseek-v4-flash",
            "api_key": "test-key",
            **extra,
        }
    )


def test_factory_and_builtin_metadata_include_current_deepseek_models(fake_client):
    provider = create_provider(
        ModelConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key="test-key",
        )
    )
    assert isinstance(provider, deepseek_module.DeepSeekProvider)
    metadata = next(
        item for item in get_builtin_providers() if item["provider_type"] == "deepseek"
    )
    assert metadata["base_url"] == "https://api.deepseek.com"
    assert [item["id"] for item in metadata["models"]] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]


def test_thinking_is_disabled_by_default_and_enabled_mode_omits_sampling(fake_client):
    disabled = make_provider(fake_client)
    payload = disabled._build_payload(
        [ChatMessage(role="user", content="hello")], None, None, 0.7, 1024
    )
    assert payload["temperature"] == 0.7
    assert payload["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in payload

    enabled = make_provider(
        fake_client,
        thinking_enabled=True,
        reasoning_effort="max",
        top_p=0.8,
    )
    payload = enabled._build_payload(
        [ChatMessage(role="user", content="hello")],
        None,
        [
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        0.7,
        1024,
    )
    assert payload["extra_body"] == {"thinking": {"type": "enabled"}}
    assert payload["reasoning_effort"] == "max"
    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "tool_choice" not in payload


def test_runtime_budget_is_capped_by_model_and_deepseek_output_limits(fake_client):
    configured = make_provider(fake_client, max_tokens=4096)
    payload = configured._build_payload(
        [ChatMessage(role="user", content="hello")],
        None,
        None,
        0.7,
        500_000,
    )
    assert payload["max_tokens"] == 4096

    provider_maximum = make_provider(fake_client, max_tokens=500_000)
    payload = provider_maximum._build_payload(
        [ChatMessage(role="user", content="hello")],
        None,
        None,
        0.7,
        500_000,
    )
    assert payload["max_tokens"] == 393_216


@pytest.mark.asyncio
async def test_invalid_internal_tool_names_are_aliased_and_restored(fake_client):
    provider = make_provider(fake_client)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "artifact.create_pdf",
                "description": "Create a PDF artifact",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    response = await provider.chat(
        messages=[ChatMessage(role="user", content="create a PDF")],
        tools=tools,
    )

    sent_name = fake_client[-1].chat.completions.calls[-1]["tools"][0]["function"]["name"]
    assert sent_name != "artifact.create_pdf"
    assert len(sent_name) <= 64
    assert all(character.isalnum() or character in "_-" for character in sent_name)
    assert response.tool_calls[0]["function"]["name"] == "artifact.create_pdf"
    assert tools[0]["function"]["name"] == "artifact.create_pdf"


@pytest.mark.asyncio
async def test_aliased_tool_name_is_reused_in_continuation_messages(fake_client):
    provider = make_provider(fake_client, thinking_enabled=True)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "file.read",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    await provider.chat(
        messages=[
            ChatMessage(
                role="assistant",
                content="",
                reasoning_content="read the requested file",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "file.read", "arguments": "{}"},
                    }
                ],
            ),
            ChatMessage(role="tool", content="contents", tool_call_id="call-1"),
        ],
        tools=tools,
    )

    call = fake_client[-1].chat.completions.calls[-1]
    sent_tool_name = call["tools"][0]["function"]["name"]
    assert call["messages"][0]["tool_calls"][0]["function"]["name"] == sent_tool_name
    assert call["messages"][0]["reasoning_content"] == "read the requested file"


@pytest.mark.asyncio
async def test_streaming_restores_aliased_tool_names(fake_client):
    provider = make_provider(fake_client)
    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            messages=[ChatMessage(role="user", content="read a file")],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "file.read",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }
            ],
        )
    ]

    sent_name = fake_client[-1].chat.completions.calls[-1]["tools"][0]["function"]["name"]
    assert sent_name != "file.read"
    tool_chunk = next(chunk for chunk in chunks if chunk.tool_call)
    assert tool_chunk.tool_call["function"]["name"] == "file.read"


def test_reasoning_content_is_returned_for_tool_call_continuation(fake_client):
    provider = make_provider(fake_client, thinking_enabled=True)
    payload = provider._build_payload(
        [
            ChatMessage(
                role="assistant",
                content="",
                reasoning_content="must be preserved",
                tool_calls=[
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": "{}"},
                    }
                ],
            ),
            ChatMessage(role="tool", content="result", tool_call_id="call-1"),
        ],
        None,
        None,
        0.7,
        None,
    )
    assert payload["messages"][0]["reasoning_content"] == "must be preserved"


@pytest.mark.asyncio
async def test_streaming_preserves_reasoning_and_fragmented_tool_calls(fake_client):
    provider = make_provider(fake_client, thinking_enabled=True)
    chunks = [
        chunk
        async for chunk in provider.chat_stream(
            messages=[ChatMessage(role="user", content="use a tool")]
        )
    ]
    assert "".join(chunk.reasoning for chunk in chunks) == "plan"
    tool_chunks = [chunk for chunk in chunks if chunk.tool_call]
    assert tool_chunks[0].tool_call["function"]["name"] == "lookup"
    assert tool_chunks[-1].finish_reason == "tool_calls"


@pytest.mark.asyncio
async def test_model_discovery_uses_openai_models_api(fake_client):
    provider = make_provider(fake_client)
    models = await provider.list_models()
    assert [item["id"] for item in models] == [
        "deepseek-v4-flash",
        "deepseek-v4-pro",
    ]
