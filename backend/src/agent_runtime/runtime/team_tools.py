"""Agent-internal collaboration tools backed by the TeamMessenger port."""

from __future__ import annotations

from ..core.types import ToolCall, ToolResult


TEAM_SEND_MESSAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "team.send_message",
        "description": "Send a non-blocking direct or broadcast message to team peers.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string"},
                "target_agent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Empty means broadcast to all other agents.",
                },
                "thread_id": {"type": "string"},
                "reply_to_message_id": {"type": "string"},
                "expects_reply": {"type": "boolean"},
            },
            "required": ["content"],
        },
    },
}

TEAM_RESOLVE_THREAD_TOOL = {
    "type": "function",
    "function": {
        "name": "team.resolve_thread",
        "description": "Record a discussion conclusion and close its thread.",
        "parameters": {
            "type": "object",
            "properties": {
                "thread_id": {"type": "string"},
                "conclusion": {"type": "string"},
            },
            "required": ["thread_id", "conclusion"],
        },
    },
}


class TeamToolExecutor:
    def __init__(self, base, messenger, agent_id: str) -> None:
        self.base = base
        self.messenger = messenger
        self.agent_id = agent_id

    def bind_agent(self, agent_id: str) -> "TeamToolExecutor":
        base = self.base
        bind_agent = getattr(base, "bind_agent", None)
        if callable(bind_agent):
            base = bind_agent(agent_id)
        return TeamToolExecutor(base, self.messenger, agent_id)

    async def list_tools(self) -> list[dict]:
        tools = list(await self.base.list_tools()) if self.base is not None else []
        names = {item.get("function", {}).get("name") for item in tools}
        if "team.send_message" not in names:
            tools.append(TEAM_SEND_MESSAGE_TOOL)
        if "team.resolve_thread" not in names:
            tools.append(TEAM_RESOLVE_THREAD_TOOL)
        return tools

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        try:
            if tool_call.tool_name == "team.send_message":
                parameters = tool_call.parameters or {}
                message = await self.messenger.send_message(
                    sender_agent_id=self.agent_id,
                    content=str(parameters.get("content") or ""),
                    recipient_agent_ids=tuple(
                        str(item) for item in parameters.get("target_agent_ids") or ()
                    ),
                    thread_id=str(parameters.get("thread_id") or "") or None,
                    reply_to_message_id=(
                        str(parameters.get("reply_to_message_id") or "") or None
                    ),
                    expects_reply=bool(parameters.get("expects_reply", False)),
                )
                return ToolResult(
                    call_id=tool_call.call_id,
                    success=True,
                    result={
                        "message_id": message.message_id,
                        "team_sequence": message.sequence,
                        "thread_id": message.thread_id,
                        "status": message.status,
                    },
                )
            if tool_call.tool_name == "team.resolve_thread":
                parameters = tool_call.parameters or {}
                await self.messenger.resolve_thread(
                    agent_id=self.agent_id,
                    thread_id=str(parameters.get("thread_id") or ""),
                    conclusion=str(parameters.get("conclusion") or ""),
                )
                return ToolResult(
                    call_id=tool_call.call_id,
                    success=True,
                    result={"thread_id": parameters.get("thread_id"), "status": "resolved"},
                )
        except Exception as exc:
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=str(exc),
            )
        if self.base is None:
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=f"Tool not found: {tool_call.tool_name}",
            )
        return await self.base.execute(tool_call)
