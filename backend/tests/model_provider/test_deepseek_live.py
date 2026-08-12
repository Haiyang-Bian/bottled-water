from __future__ import annotations

import os

import pytest

from model_provider import ChatMessage, ModelConfig, create_provider


pytestmark = [pytest.mark.live, pytest.mark.providers]


@pytest.mark.asyncio
async def test_deepseek_live_smoke():
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        pytest.skip("DEEPSEEK_API_KEY is required for the explicit live test group")
    provider = create_provider(
        ModelConfig(
            provider="deepseek",
            model="deepseek-v4-flash",
            api_key=api_key,
            extra={"thinking_enabled": False},
        )
    )
    response = await provider.chat(
        messages=[ChatMessage(role="user", content="Reply with exactly: ok")],
        max_tokens=16,
    )
    assert response.content.strip()
