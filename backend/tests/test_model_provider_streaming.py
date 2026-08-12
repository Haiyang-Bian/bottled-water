import pytest

from model_provider.core.interfaces import BaseModelProvider, ChatMessage, StreamChunk
from model_provider.core.streaming import collect_chat_stream


class FragmentedToolProvider(BaseModelProvider):
    async def chat(self, *args, **kwargs):  # pragma: no cover - this helper must not use chat
        raise AssertionError("collect_chat_stream must consume chat_stream")

    async def chat_stream(self, *args, **kwargs):
        yield StreamChunk(
            tool_call={
                "id": "call-1",
                "type": "function",
                "function": {"name": "lookup", "arguments": '{"query":'},
            }
        )
        yield StreamChunk(tool_call={"function": {"arguments": '"hello"'}})
        yield StreamChunk(tool_call={"function": {"arguments": "}"}})
        yield StreamChunk(content="done")


class ReasoningProvider(BaseModelProvider):
    async def chat(self, *args, **kwargs):  # pragma: no cover - this helper must not use chat
        raise AssertionError("collect_chat_stream must consume chat_stream")

    async def chat_stream(self, *args, **kwargs):
        yield StreamChunk(reasoning="private reasoning")
        yield StreamChunk(content="public answer", finish_reason="stop")


@pytest.mark.asyncio
async def test_collect_chat_stream_merges_fragmented_tool_calls():
    provider = FragmentedToolProvider({"model": "test-model"})

    response = await collect_chat_stream(
        provider,
        messages=[ChatMessage(role="user", content="run a tool")],
    )

    assert response.content == "done"
    assert response.tool_calls == [
        {
            "id": "call-1",
            "type": "function",
            "function": {"name": "lookup", "arguments": '{"query":"hello"}'},
        }
    ]


@pytest.mark.asyncio
async def test_collect_chat_stream_keeps_reasoning_separate_from_content():
    provider = ReasoningProvider({"model": "test-model"})

    response = await collect_chat_stream(
        provider,
        messages=[ChatMessage(role="user", content="think")],
    )

    assert response.content == "public answer"
    assert response.reasoning_content == "private reasoning"
