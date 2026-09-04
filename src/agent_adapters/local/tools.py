"""Compose local drivers through the shared registry and authorization boundary."""

import base64

from agent_contracts.execution import ExecutionContext, ToolSpec
from agent_subsystems.tools.registry import ToolRegistry
from agent_subsystems.tools.invoker import AuthorizedToolInvoker
from agent_subsystems.workspaces.paths import resolve_resource
from .files import LocalFiles
from .processes import executable, powershell_executable


class TrustAuthorization:
    def __init__(self, store):
        self.store = store

    def authorize(self, spec, context):
        if spec.capability not in context.grant.capabilities:
            return "deny"
        if not all(self.store.is_trusted(root) for root in context.grant.workspace.roots):
            return "requires_user"
        return "allow"


class LocalToolExecutor:
    def __init__(self, grant, authorization, process_driver, redactor, *, shell=None):
        self.grant = grant
        self.authorization = authorization
        self.process_driver = process_driver
        self.redactor = redactor
        self.shell = shell

    def bind_execution(self, request, cancellation, lease):
        context = ExecutionContext(
            request.run_id,
            request.context_scope_id,
            request.agent.id,
            self.grant,
            float(request.metadata["execution_deadline"]),
            cancellation,
            lease,
        )
        registry, specs = ToolRegistry(), {}
        files = LocalFiles(self.grant.workspace)

        def register(name, description, handler, properties, required, capability):
            schema = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            specs[name] = ToolSpec(name, description, schema, capability)
            registry.register(name, description, schema, handler)

        string = {"type": "string"}
        integer = {"type": "integer"}
        register(
            "file.read",
            "Read text with line numbers and a sha256 for subsequent edits.",
            files.read,
            {"path": string, "start_line": integer, "limit": integer},
            ["path"],
            "files",
        )
        register(
            "file.write",
            "Create or replace text. expected_hash must be the last read sha256, or 'new'.",
            files.write,
            {"path": string, "content": string, "expected_hash": string},
            ["path", "content", "expected_hash"],
            "files",
        )
        register(
            "file.edit",
            "Replace exactly one old_text match; provide the last read sha256.",
            files.edit,
            {"path": string, "old_text": string, "new_text": string, "expected_hash": string},
            ["path", "old_text", "new_text", "expected_hash"],
            "files",
        )
        register(
            "file.list",
            "List file paths, skipping generated and dependency directories.",
            files.list,
            {"path": string, "pattern": string, "offset": integer, "limit": integer},
            [],
            "files",
        )
        register(
            "file.search",
            "Search literal text with file paths and line numbers.",
            files.search,
            {
                "query": string,
                "path": string,
                "pattern": string,
                "offset": integer,
                "limit": integer,
            },
            ["query"],
            "files",
        )

        async def powershell(script, cwd=".", timeout=120):
            directory = resolve_resource(self.grant.workspace, cwd, directory=True)
            prefix = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $OutputEncoding = [Console]::OutputEncoding; $ErrorActionPreference = 'Stop'; $global:LASTEXITCODE = 0;\n"
            code = (
                prefix
                + "try {\n"
                + script
                + "\nif ($global:LASTEXITCODE -ne 0) { exit $global:LASTEXITCODE }\n} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }"
            )
            encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
            return await self.process_driver.run(
                [
                    powershell_executable(self.shell),
                    "-NoLogo",
                    "-NoProfile",
                    "-NonInteractive",
                    "-EncodedCommand",
                    encoded,
                ],
                directory,
                timeout=timeout,
                context=context,
            )

        async def git(args, cwd=".", timeout=120):
            directory = resolve_resource(self.grant.workspace, cwd, directory=True)
            return await self.process_driver.run(
                [executable("git"), "--no-pager", *args],
                directory,
                timeout=timeout,
                context=context,
                env={"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"},
            )

        common = {"cwd": string, "timeout": {"type": "number"}}
        register(
            "powershell.run",
            "Execute a non-interactive PowerShell script with current-user permissions. Supports pipes and multiline scripts; no OS sandbox.",
            powershell,
            {"script": string, **common},
            ["script"],
            "process",
        )
        register(
            "git.run",
            "Execute Git with an argument array; inspect exit_code. Commit/push/reset only if the user requested it.",
            git,
            {"args": {"type": "array", "items": string}, **common},
            ["args"],
            "process",
        )
        return AuthorizedToolInvoker(registry, specs, self.authorization, context, self.redactor)
