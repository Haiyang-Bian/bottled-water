"""Keep configured credentials and private reasoning out of local events."""

from dataclasses import replace
from model_provider.core.interfaces import BaseModelProvider, StreamChunk
from agent_subsystems.observability.redaction import RedactedStream


class LocalModelProvider(BaseModelProvider):
    def __init__(self, provider, redactor):
        super().__init__({"model": provider.model})
        self.provider, self.redactor = provider, redactor

    async def chat(self, *args, **kwargs):
        response = await self.provider.chat(*args, **kwargs)
        return replace(
            response,
            content=self.redactor.text(response.content),
            reasoning_content="",
            tool_calls=self.redactor.value(response.tool_calls),
        )

    async def chat_stream(self, *args, **kwargs):
        stream = self.provider.chat_stream(*args, **kwargs)
        redacted = RedactedStream(self.redactor)
        try:
            async for chunk in stream:
                yield replace(
                    chunk,
                    content=redacted.push(chunk.content),
                    tool_call=self.redactor.value(chunk.tool_call),
                )
            tail = redacted.push("", final=True)
            if tail:
                yield StreamChunk(content=tail)
        finally:
            await stream.aclose()

    def count_tokens(self, text):
        return self.provider.count_tokens(text)

    async def aclose(self):
        await self.provider.aclose()
