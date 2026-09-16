"""Read saved tool observations within one authorized context scope."""

import json

from agent_contracts.errors import OperationError
from agent_contracts.execution import AuthorizationRequest, ToolSpec
from agent_runtime.core.types import ToolResult
from .invoker import validate


NAME = "run.read_tool_result"
PARAMETERS = {
    "type": "object",
    "properties": {
        "run_id": {"type": "string"},
        "call_id": {"type": "string"},
        "offset": {"type": "integer"},
        "limit": {"type": "integer"},
    },
    "required": ["run_id", "call_id"],
    "additionalProperties": False,
}
DESCRIPTION = "Read a saved tool result from this session only, in character pages. Saved output may itself be truncated."


class JournalResultReader:
    def __init__(self, journal, scope_id):
        self.journal, self.scope_id = journal, scope_id

    async def read(self, run_id, call_id, offset=0, limit=8000):
        if offset < 0 or not 1 <= limit <= 12000:
            raise OperationError("invalid_range", "Invalid result page")
        cursor = 0
        while True:
            try:
                page = await self.journal.read_events(run_id, after_sequence=cursor)
            except KeyError as exc:
                raise OperationError("result_not_found", "No accessible tool result") from exc
            for event in page.items:
                if event.context_scope_id != self.scope_id:
                    raise OperationError("result_not_found", "No accessible tool result")
                if event.type != "agent.tool_result" or event.payload.get("call_id") != call_id:
                    continue
                payload = event.payload
                text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                end = min(len(text), offset + limit)
                result = payload.get("result")
                return {
                    "run_id": run_id,
                    "call_id": call_id,
                    "offset": offset,
                    "result_json": text[offset:end],
                    "total_chars": len(text),
                    "next_offset": end if end < len(text) else None,
                    "truncated": end < len(text),
                    "record_truncated": bool(isinstance(result, dict) and result.get("truncated")),
                }
            if not page.items:
                raise OperationError("result_not_found", "No accessible tool result")
            cursor = page.next_sequence


class HistoryToolExecutor:
    def __init__(self, delegate, reader):
        self.delegate, self.reader = delegate, reader

    def bind_agent(self, agent_id):
        bind = getattr(self.delegate, "bind_agent", None)
        return HistoryToolExecutor(bind(agent_id) if callable(bind) else self.delegate, self.reader)

    async def list_tools(self):
        existing = await self.delegate.list_tools() if self.delegate else []
        return [t for t in existing if t.get("function", {}).get("name") != NAME] + [
            {
                "type": "function",
                "function": {"name": NAME, "description": DESCRIPTION, "parameters": PARAMETERS},
            }
        ]

    async def execute(self, call):
        if call.tool_name != NAME:
            if self.delegate:
                return await self.delegate.execute(call)
            return ToolResult(call.call_id, False, error="Tool is not registered")
        try:
            validate(call.parameters, PARAMETERS)
            # CLI invokers recheck trust on every read; Web scopes are authorized by their host.
            context = getattr(self.delegate, "context", None)
            authorization = getattr(self.delegate, "authorization", None)
            if context is not None:
                context.check()
                if (
                    authorization.authorize(
                        AuthorizationRequest(ToolSpec(NAME, DESCRIPTION, PARAMETERS, "files"),
                                             context, call.parameters)
                    )
                    != "allow"
                ):
                    raise OperationError(
                        "authorization_required", "Session resources are not trusted"
                    )
            result = await self.reader.read(**call.parameters)
            if context is not None:
                context.check()
            return ToolResult(call.call_id, True, result)
        except OperationError as exc:
            return ToolResult(call.call_id, False, {"error_code": exc.code}, str(exc))
