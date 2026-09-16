"""Invocation boundary around the shared tool registry and executor."""

from agent_contracts.errors import OperationError
from agent_contracts.execution import AuthorizationRequest
from agent_runtime.core.types import ToolResult
from .executor import ToolExecutorImpl


def validate(value, schema):
    kind = schema.get("type")
    types = {
        "object": dict,
        "array": list,
        "string": str,
        "number": (int, float),
        "integer": int,
        "boolean": bool,
    }
    if kind in types and (
        not isinstance(value, types[kind])
        or (kind in {"number", "integer"} and isinstance(value, bool))
    ):
        raise OperationError("invalid_arguments", f"Expected {kind}")
    if kind == "object":
        props = schema.get("properties", {})
        if set(schema.get("required", ())) - value.keys():
            raise OperationError("invalid_arguments", "Missing required parameters")
        if schema.get("additionalProperties") is False and value.keys() - props.keys():
            raise OperationError("invalid_arguments", "Unknown parameters")
        for key, item in value.items():
            if key in props:
                validate(item, props[key])
    if kind == "array":
        for item in value:
            validate(item, schema.get("items", {}))


class AuthorizedToolInvoker:
    def __init__(self, registry, specs, authorization, context, redactor):
        self.registry = registry
        self.executor = ToolExecutorImpl(registry)
        self.specs = specs
        self.authorization = authorization
        self.context = context
        self.redactor = redactor

    async def list_tools(self):
        return self.registry.list_tools()

    async def execute(self, call):
        try:
            self.context.check()
            spec = self.specs.get(call.tool_name)
            if spec is None:
                raise OperationError("unknown_tool", "Tool is not registered")
            validate(call.parameters, spec.parameters)
            operation = "modify" if spec.name in {"file.write", "file.edit"} else "read"
            target = call.parameters.get("cwd" if spec.capability == "process" else "path", ".")
            decision = self.authorization.authorize(
                AuthorizationRequest(spec, self.context, call.parameters, operation, target))
            if decision != "allow":
                raise OperationError("authorization_required", "Tool capability is not authorized")
            result = await self.executor.execute(call)
            self.context.check()
            result.result = self.redactor.value(result.result)
            result.error = self.redactor.text(result.error) if result.error else None
            if isinstance(result.result, dict) and result.result.get("exit_code", 0) != 0:
                result.success = False
                result.error = f"Command exited with code {result.result['exit_code']}"
            return result
        except OperationError as exc:
            return ToolResult(call.call_id, False, {"error_code": exc.code}, str(exc))
