"""Adapter from ``agent_runtime`` tool calls to AgentHub services."""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.core.interfaces import ToolExecutor
from agent_runtime.core.types import ToolCall
from common.logger import get_logger
from db.models import Agent, Conversation

logger = get_logger(__name__)


class ToolExecutorAdapter(ToolExecutor):
    """Bridge the runtime ToolExecutor interface to AgentHub tool services."""

    def __init__(
        self,
        db: AsyncSession,
        agent: Agent,
        user: Any,
        conversation: Conversation,
    ):
        self.db = db
        self.agent = agent
        self.user = user
        self.conversation = conversation
        self._tools_cache: list[dict] | None = None

    async def list_tools(self) -> list[dict]:
        """Return OpenAI Function Calling style tool schemas for the agent."""
        if self._tools_cache is None:
            from app.services.agents.async_tool_loop import build_tools_for_agent

            self._tools_cache = await build_tools_for_agent(self.db, self.agent)
        return self._tools_cache

    async def execute(self, tool_call: ToolCall) -> Any:
        """Execute one runtime tool call through the AgentHub tool loop."""
        from app.services.agents.async_tool_loop import execute_tool_by_name

        logger.info(
            "Runtime adapter executing tool",
            tool=tool_call.tool_name,
            agent=self.agent.name,
        )
        return await execute_tool_by_name(
            self.db,
            agent=self.agent,
            user=self.user,
            conversation=self.conversation,
            tool_name=tool_call.tool_name,
            arguments={**tool_call.parameters, "agent_id": str(self.agent.id)},
        )
