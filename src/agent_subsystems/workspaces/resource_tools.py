"""Resource queries and verified software calls; no model-controlled registration/grants."""

from dataclasses import asdict

from agent_contracts.errors import OperationError
from agent_contracts.execution import AuthorizationRequest, ToolSpec
from agent_contracts.resources import ResourceAccessContext, ResourceSource
from agent_runtime.core.types import ToolResult
from agent_subsystems.memory.tools import spec
from agent_subsystems.tools.invoker import validate
from agent_subsystems.workspaces.paths import resolve_resource

STRING = {"type": "string"}
QUERY = {"query": STRING, "offset": {"type": "integer"}, "limit": {"type": "integer"}}
SPECS = [
    spec(
        "resource.search",
        "Search saved resource metadata across tasks; grants no file access.",
        QUERY,
    ),
    spec(
        "resource.read",
        "Read saved resource location, observation and source. Current state is not checked.",
        {"id": STRING},
        ["id"],
    ),
    spec(
        "resource.verify",
        "Probe a resource only within current file grants; detect missing/changed files.",
        {"id": STRING},
        ["id"],
    ),
    spec("software.search", "Find user-enabled Python, uv or Git registrations.", QUERY),
    spec(
        "software.read",
        "Read a user-enabled executable registration and version.",
        {"id": STRING},
        ["id"],
    ),
    spec(
        "software.run",
        "Execute an enabled software ID using an argument array. Declare output paths to inspect before/after. No automatic installation or file authority. Handle software_changed by asking user to verify the registration.",
        {
            "id": STRING,
            "args": {"type": "array", "items": STRING},
            "cwd": STRING,
            "timeout": {"type": "number"},
            "outputs": {"type": "array", "items": STRING},
        },
        ["id", "args"],
    ),
    spec(
        "task.search",
        "Find previous tasks by names, requests, resources and dates (今天/昨天/前天/最近N天). Does not resume or merge history.",
        {**QUERY, "since": STRING, "until": STRING},
    ),
    spec(
        "task.read",
        "Read a bounded prior task summary. Assistant statements are not verified facts. Unknown operations must be checked before retry. Use /resume QUERY for full history.",
        {"id": STRING},
        ["id"],
    ),
]


class ResourceToolExecutor:
    def __init__(self, delegate, resources, access, software, tasks, probe, redactor,
                 file_operations=None):
        self.delegate, self.resources, self.access = delegate, resources, access
        self.software, self.tasks, self.probe, self.redactor = software, tasks, probe, redactor
        self.context = getattr(delegate, "context", None)
        self.authorization = getattr(delegate, "authorization", None)
        self.file_operations = file_operations

    def bind_execution(self, request, cancellation, lease):
        bound = self.delegate.bind_execution(request, cancellation, lease)
        access = ResourceAccessContext(
            self.access.environment_id, request.agent.id, request.context_scope_id, request.run_id
        )
        return ResourceToolExecutor(
            bound, self.resources, access, self.software, self.tasks, self.probe, self.redactor,
            self.file_operations,
        )

    async def list_tools(self):
        return [*await self.delegate.list_tools(), *SPECS]

    async def execute(self, call):
        schema = next(
            (s["function"]["parameters"] for s in SPECS if s["function"]["name"] == call.tool_name),
            None,
        )
        if schema is None:
            return await self.delegate.execute(call)
        try:
            self.context.check()
            self.resources.check(self.access)
            validate(call.parameters, schema)
            args, name = dict(call.parameters), call.tool_name
            capability = (
                "files"
                if name == "resource.verify"
                else "process"
                if name == "software.run"
                else None
            )
            if (
                capability
                and self.authorization.authorize(
                    AuthorizationRequest(ToolSpec(name, "", schema, capability),
                                         self.context, args)
                )
                != "allow"
            ):
                raise OperationError(
                    "authorization_required", "Current resource capability is not authorized"
                )
            if name in {"resource.search", "software.search"}:
                software = name.startswith("software")
                records = self.resources.search(
                    self.access, **args, kind="software" if software else None
                )
                values = []
                for record in records:
                    if software:
                        try:
                            _, cfg = self.resources.software(self.access, record.id)
                        except OperationError:
                            continue
                        values.append({**asdict(record), "software": asdict(cfg)})
                    else:
                        values.append(asdict(record))
                result = {
                    "resource_records": values,
                    "page_limit": args.get("limit", 20),
                    "offset": args.get("offset", 0),
                    "may_have_more": len(records) == args.get("limit", 20),
                    "notice": "Saved metadata; filesystem state is not checked",
                }
            elif name in {"resource.read", "software.read"}:
                record = self.resources.read(self.access, args["id"])
                value = asdict(record)
                if name == "software.read":
                    _, cfg = self.resources.software(self.access, args["id"])
                    value["software"] = asdict(cfg)
                result = {
                    "resource_records": [value],
                    "notice": "Saved metadata; no file authority",
                }
            elif name == "resource.verify":
                record = self.resources.read(self.access, args["id"])
                if self.file_operations is not None:
                    facts = await self.file_operations.invoke(
                        "probe", {"path": record.content.path}, self.context)
                    path = facts.pop("path")
                else:
                    path = resolve_resource(
                        self.context.grant.workspace, self.context.location, record.content.path
                    )
                    facts = await self.probe(path, self.context)
                record = self.resources.observe(
                    self.access,
                    str(path),
                    facts,
                    ResourceSource(
                        "verification",
                        self.access.run_id,
                        call_id=call.call_id,
                        operation_id=call.call_id,
                    ),
                )
                result = {"resource_records": [asdict(record)]}
            elif name == "software.run":
                identifier = args.pop("id")
                _, cfg = self.resources.software(self.access, identifier)
                result = await self.software.run(cfg, context=self.context, **args)
            elif name == "task.search":
                result = {"task_records": self.tasks.search(self.access, **args)}
            else:
                result = {"task_records": [self.tasks.read(self.access, args["id"])]}
            self.context.check()
            result = self.redactor.value(result)
            ok = result.get("exit_code", 0) == 0
            return ToolResult(
                call.call_id,
                ok,
                result,
                None if ok else f"Command exited with code {result['exit_code']}",
            )
        except (OperationError, OSError, ValueError, TypeError) as exc:
            return ToolResult(
                call.call_id,
                False,
                {"error_code": getattr(exc, "code", "resource_error")},
                str(exc),
            )
