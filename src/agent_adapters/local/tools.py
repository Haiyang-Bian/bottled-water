"""Compose local drivers through the shared registry and authorization boundary."""

import base64

from agent_contracts.errors import OperationError
from agent_contracts.execution import ExecutionContext, ToolSpec
from agent_subsystems.tools.registry import ToolRegistry
from agent_subsystems.tools.invoker import AuthorizedToolInvoker
from .file_operations import BoundFileOperations, LocalFileOperations
from .processes import executable, powershell_executable


class TrustAuthorization:
    def __init__(self, store):
        self.store = store

    def authorize(self, request):
        spec, context = request.spec, request.context
        if spec.capability not in context.grant.capabilities:
            return "deny"
        if context.grant.file_access_scope == "user":
            return "allow"
        if not all(self.store.is_trusted(root) for root in context.grant.workspace.roots):
            return "requires_user"
        return "allow"


async def read_git_index(driver, directory, context):
    outcome = await driver.run(
        [executable("git"), "ls-files", "--cached", "-z"], directory,
        timeout=10, context=context,
        env={"GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C"},
    )
    if outcome.get("exit_code") != 0:
        return [], ("not_repository" if "not a git repository" in
                    outcome.get("stderr", "").lower() else "failed")
    return outcome.get("stdout", "").split("\0")[:-1], (
        "truncated" if outcome.get("truncated") else "complete"
    )


class LocalToolExecutor:
    def __init__(self, grant, location, authorization, process_driver, redactor, *, shell=None,
                 file_operations=None, executables=None):
        self.grant = grant
        self.location = location
        self.authorization = authorization
        self.process_driver = process_driver
        self.redactor = redactor
        self.shell = shell
        self.file_operations = file_operations
        self.executables = executables

    def bind_execution(self, request, cancellation, lease):
        context = ExecutionContext(
            request.run_id,
            request.context_scope_id,
            request.agent.id,
            self.grant,
            float(request.metadata["execution_deadline"]),
            cancellation,
            lease,
            self.location,
        )
        registry, specs = ToolRegistry(), {}

        async def read_index(directory):
            return await read_git_index(self.process_driver, directory, context)

        files = BoundFileOperations(
            self.file_operations or LocalFileOperations(index_reader=read_index), context)

        def selected_executable(kind):
            if self.executables is not None:
                selected = self.executables.get(kind)
                if not selected:
                    raise OperationError("software_incompatible", "No verified isolated " + kind + " copy")
                return selected
            return powershell_executable(self.shell) if kind == "pwsh" else executable(kind)

        def register(name, description, handler, properties, required, capability):
            schema = {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            }
            specs[name] = ToolSpec(name, description, schema, capability)
            async def invoke(**kwargs):
                execution = {"default_cwd": str(context.location.cwd),
                             "workspace_version": context.location.version}
                if capability == "process":
                    execution["cwd"] = str(await files.resolve(
                        kwargs.get("cwd", "."),
                        directory=True,
                    ))
                else:
                    execution["path"] = str(await files.resolve(kwargs.get("path", ".")))
                result = await handler(**kwargs)
                if isinstance(result, dict):
                    result["execution"] = {**execution, **result.get("execution", {})}
                return result

            registry.register(name, description, schema, invoke)

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
            "List files and directories at one level by default. Set recursive=true to find nested sources; include_ignored=true to inspect excluded content. Inspect discovery diagnostics and pagination.",
            files.list,
            {
                "path": string,
                "pattern": string,
                "offset": integer,
                "limit": integer,
                "recursive": {"type": "boolean"},
                "include_ignored": {"type": "boolean"},
            },
            [],
            "files",
        )
        register(
            "file.search",
            "Search literal text recursively in discoverable source files. include_ignored opts into excluded content; inspect discovery diagnostics and pagination.",
            files.search,
            {
                "query": string,
                "recursive": {"type": "boolean"},
                "include_ignored": {"type": "boolean"},
                "path": string,
                "pattern": string,
                "offset": integer,
                "limit": integer,
            },
            ["query"],
            "files",
        )

        async def powershell(script, cwd=".", timeout=120):
            directory = await files.resolve(cwd, directory=True)
            prefix = "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false); $OutputEncoding = [Console]::OutputEncoding; $ErrorActionPreference = 'Stop'; $global:LASTEXITCODE = 0;\n"
            location_setup = ""
            if self.executables is not None:
                # A PSDrive rooted at the approved cwd avoids walking ungranted
                # ancestors while PowerShell normalizes its initial location.
                literal = str(directory).replace("'", "''")
                location_setup = (
                    f"New-PSDrive -Name AgentHub -PSProvider FileSystem -Root '{literal}' "
                    "-Scope Global -ErrorAction Stop | Out-Null;\n"
                    "Set-Location -LiteralPath 'AgentHub:\\' -ErrorAction Stop;\n"
                )
            code = (
                prefix
                + "try {\n"
                + location_setup
                + script
                + "\nif ($global:LASTEXITCODE -ne 0) { exit $global:LASTEXITCODE }\n} catch { [Console]::Error.WriteLine($_.ToString()); exit 1 }"
            )
            encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
            return await self.process_driver.run(
                [
                    selected_executable("pwsh"),
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
            directory = await files.resolve(cwd, directory=True)
            return await self.process_driver.run(
                [selected_executable("git"), "--no-pager", *args],
                directory,
                timeout=timeout,
                context=context,
                env={"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"},
            )

        common = {"cwd": string, "timeout": {"type": "number"}}
        register(
            "powershell.run",
            ("Execute PowerShell 7 inside the Windows restricted driver; offline with frozen file permissions. "
             "Initial location is the temporary AgentHub: drive rooted at cwd. "
             "Use (Get-Location).ProviderPath for its actual filesystem path."
             if self.grant.execution_mode == "windows_lpac" else
             "Execute a non-interactive PowerShell script with current-user permissions. Supports pipes and multiline scripts; no OS sandbox."),
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
        if self.grant.execution_mode == "current_user" and self.grant.file_access_scope == "user":
            from .resources import LocalSoftware, discover
            software = LocalSoftware(self.process_driver)

            async def native(executable, args, cwd=".", timeout=120, outputs=()):
                return await software.run_native(executable, args, context, cwd=cwd,
                                                 timeout=timeout, outputs=outputs)

            async def software_discover(kind, cwd=".", path=None):
                directory = await files.resolve(cwd, directory=True)
                return {"candidates": discover(kind, directory, path),
                        "registered": False, "execution": {"cwd": str(directory)},
                        "notice": "Discovery only. Use process.run with the selected actual path; "
                                  "AgentHub's interpreter may differ from the project's Python."}

            register("process.run", "Execute a local executable directly with an argument array, "
                     "ordinary user permissions and network access. No software ID required. "
                     "Use an explicit project interpreter or PATH name; no implicit shell. "
                     "Declare outputs for before/after observations. Batch scripts use powershell.run.",
                     native, {"executable": string, "args": {"type": "array", "items": string},
                              "outputs": {"type": "array", "items": string}, **common},
                     ["executable", "args"], "process")
            register("software.discover", "Discover Python, uv or Git in the project .venv, PATH "
                     "and AgentHub interpreter. Returns candidates with sources, without running "
                     "or registering them. kind: python|uv|git.", software_discover,
                     {"kind": string, "cwd": string, "path": string}, ["kind"], "files")
        return AuthorizedToolInvoker(registry, specs, self.authorization, context, self.redactor)
