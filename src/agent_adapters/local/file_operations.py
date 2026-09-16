"""File operation port using the existing current-user algorithms."""

from pathlib import Path

from agent_contracts.errors import OperationError
from agent_subsystems.workspaces.paths import resolve_resource
from .files import LocalFiles
from .file_probes import probe


class LocalFileOperations:
    def __init__(self, *, index_reader=None):
        self.index_reader = index_reader

    async def invoke(self, operation, parameters, context):
        context.check()
        if operation == "resolve":
            path = resolve_resource(context.grant.workspace, context.location, **parameters)
            return {"path": str(path)}
        if operation == "probe":
            args = dict(parameters)
            path = resolve_resource(context.grant.workspace, context.location, args.pop("path"))
            return {"path": str(path), **await probe(path, context, **args)}
        if operation not in {"read", "write", "edit", "list", "search"}:
            raise OperationError("invalid_operation", "Unsupported file operation")
        files = LocalFiles(context.grant.workspace, context.location, index_reader=self.index_reader)
        result = await getattr(files, operation)(**parameters)
        context.check()
        return result


class BoundFileOperations:
    """A per-execution facade; the port chooses where all path I/O happens."""

    def __init__(self, port, context):
        self.port, self.context = port, context

    async def resolve(self, value, *, directory=False):
        result = await self.port.invoke("resolve", {"value": value, "directory": directory},
                                        self.context)
        return Path(result["path"])

    async def probe(self, path, **kwargs):
        return await self.port.invoke("probe", {"path": str(path), **kwargs}, self.context)

    def __getattr__(self, name):
        if name not in {"read", "write", "edit", "list", "search"}:
            raise AttributeError(name)

        async def execute(**kwargs):
            return await self.port.invoke(name, kwargs, self.context)

        return execute
