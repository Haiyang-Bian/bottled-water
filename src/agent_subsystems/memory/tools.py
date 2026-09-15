"""Read/propose tools only. Approval and identity are never model-controlled."""

from dataclasses import asdict, replace

from agent_contracts.errors import OperationError
from agent_contracts.memory import MemoryAccessContext, MemoryRevision, MemorySource
from agent_runtime.core.types import ToolResult
from agent_subsystems.tools.invoker import validate


FIELDS = {
    "title": {"type": "string"},
    "body": {"type": "string"},
    "kind": {"type": "string", "enum": ["preference", "environment", "decision", "experience"]},
    "evidence": {"type": "string", "enum": ["user_stated", "observed", "inferred"]},
    "tags": {"type": "array", "items": {"type": "string"}},
    "aliases": {"type": "array", "items": {"type": "string"}},
    "directory": {"type": "string"},
    "sources": {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["request", "tool", "memory"]},
                "run_id": {"type": "string"},
                "sequence": {"type": "integer"},
                "call_id": {"type": "string"},
                "memory_id": {"type": "string"},
                "revision": {"type": "integer"},
            },
            "required": ["kind"],
            "additionalProperties": False,
        },
    },
}


def spec(name, description, fields, required=()):
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": fields,
                "required": list(required),
                "additionalProperties": False,
            },
        },
    }


SPECS = [
    spec(
        "memory.search",
        "Search approved knowledge across tasks, independent of file access. Up to 20 per page.",
        {"query": {"type": "string"}, "offset": {"type": "integer"}, "limit": {"type": "integer"}},
    ),
    spec(
        "memory.read",
        "Read an approved memory with provenance; grants no access to its source files.",
        {"id": {"type": "string"}},
        ("id",),
    ),
    spec(
        "memory.propose",
        "Save a candidate awaiting user approval, never active memory. For 'remember' requests propose a user_stated quote from the request. Observed content must quote saved tool facts; conclusions are inferred. Copy the source_ref object from the tool result into sources. Never invent call IDs; sequence is optional. At most 10 per Run. Never claim permanently remembered until user adoption.",
        FIELDS,
        ("title", "body", "kind", "evidence", "sources"),
    ),
]


class MemoryToolExecutor:
    def __init__(self, delegate, memory, access, *, context=None):
        self.delegate, self.memory, self.access = delegate, memory, access
        self.context = context or getattr(delegate, "context", None)
        self.authorization = getattr(delegate, "authorization", None)

    def bind_execution(self, request, cancellation, lease):
        bound = self.delegate.bind_execution(request, cancellation, lease)
        access = MemoryAccessContext(
            self.access.environment_id, request.agent.id, request.context_scope_id, request.run_id
        )
        return MemoryToolExecutor(bound, self.memory, access)

    async def list_tools(self):
        return [*await self.delegate.list_tools(), *SPECS]

    async def execute(self, call):
        schema = next(
            (s["function"]["parameters"] for s in SPECS if s["function"]["name"] == call.tool_name),
            None,
        )
        if schema is None:
            result = await self.delegate.execute(call)
            if isinstance(result.result, dict) and self.access.run_id:
                result = replace(
                    result,
                    result={
                        **result.result,
                        "source_ref": {
                            "kind": "tool",
                            "run_id": self.access.run_id,
                            "call_id": call.call_id,
                        },
                    },
                )
            return result
        try:
            if self.context:
                self.context.check()
            validate(call.parameters, schema)
            self.memory.check(self.access)
            args = dict(call.parameters)
            if call.tool_name == "memory.search":
                records = self.memory.search(self.access, **args)
                result = {
                    "memory_records": [asdict(r) for r in records],
                    "offset": args.get("offset", 0),
                    "page_limit": args.get("limit", 20),
                    "may_have_more": len(records) == args.get("limit", 20),
                }
            elif call.tool_name == "memory.read":
                result = {"memory_records": [asdict(self.memory.read(self.access, args["id"]))]}
            else:
                sources = [MemorySource(**s) for s in args.pop("sources")]
                result = asdict(
                    self.memory.propose(self.access, call.call_id, MemoryRevision(**args), sources)
                )
                result["notice"] = (
                    "Candidate saved; pending validation and user adoption. Not active memory."
                )
            if self.context:
                self.context.check()
            return ToolResult(call.call_id, True, result)
        except (OperationError, TypeError, ValueError) as exc:
            return ToolResult(
                call.call_id,
                False,
                {"error_code": getattr(exc, "code", "invalid_memory")},
                str(exc),
            )
