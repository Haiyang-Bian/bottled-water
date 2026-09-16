"""Bounded native resource probes and software calls using the existing process driver."""

import os
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agent_contracts.errors import OperationError
from agent_contracts.resources import SoftwareSpec
from agent_runtime.core.run_types import utc_now
from agent_runtime.runtime.cancellation import CancellationScope
from agent_subsystems.workspaces.paths import resolve_resource
from .file_probes import check, probe
from .processes import native_executable


@dataclass
class ManagementOperation:
    """A user command lifetime, not a fabricated chat Run or persisted session."""

    deadline: float
    cancellation: CancellationScope
    operation_id: str

    def check(self):
        self.cancellation.raise_if_cancelled()
        if time.monotonic() >= self.deadline:
            raise OperationError("operation_timeout", "Management operation deadline expired")


def management_operation(timeout=30):
    return ManagementOperation(time.monotonic() + timeout, CancellationScope(), str(uuid4()))


def discover(kind, cwd, explicit=None):
    if kind not in {"python", "uv", "git"}:
        raise OperationError("software_kind", "Supported software: python, uv, git")
    candidates = []
    if explicit:
        value = Path(explicit).expanduser()
        candidates.append((value if value.is_absolute() else Path(cwd) / value, "explicit"))
    else:
        if kind == "python":
            candidates.extend(
                [
                    (Path(cwd)
                    / ".venv"
                    / ("Scripts/python.exe" if os.name == "nt" else "bin/python"), "project_venv"),
                    (Path(sys.executable), "agenthub_interpreter"),
                ]
            )
        for directory in os.get_exec_path():
            found = shutil.which(kind, path=directory)
            if found:
                candidates.append((Path(found), "path"))
    result, seen = [], set()
    for path, source in candidates:
        absolute = os.path.normcase(str(path.resolve()))
        if absolute in seen or not path.exists():
            continue
        seen.add(absolute)
        placeholder = "\\microsoft\\windowsapps\\" in absolute.lower().replace("/", "\\")
        result.append(
            {
                "kind": kind,
                "source": source,
                "path": absolute,
                "verified": False,
                "notice": "WindowsApps alias: choose a real executable"
                if placeholder
                else "Discovered only; registration is optional for native execution",
                "placeholder": placeholder,
            }
        )
    return result


class LocalSoftware:
    def __init__(self, driver):
        self.driver = driver

    async def verify(self, record, kind, cwd, context):
        if kind not in {"python", "uv", "git"}:
            raise OperationError("software_kind", "Supported software: python, uv, git")
        path = Path(record.content.path)
        if "\\microsoft\\windowsapps\\" in str(path).lower().replace("/", "\\"):
            raise OperationError(
                "software_alias", "Choose a real interpreter, not a WindowsApps alias"
            )
        before = await probe(path, context, hash_limit=None)
        if not before.get("sha256"):
            raise OperationError("software_missing", "Executable is missing or not a file")
        output = await self.driver.run(
            [str(path), "--version"], Path(cwd), timeout=10, context=context
        )
        if output["exit_code"] != 0:
            raise OperationError("software_probe_failed", "Version command failed")
        version = (output["stdout"] or output["stderr"]).strip()[:400]
        if not version:
            raise OperationError("software_probe_failed", "Version command returned no version")
        prefix = {"python": "Python ", "uv": "uv ", "git": "git version "}[kind]
        if not version.startswith(prefix):
            raise OperationError(
                "software_kind_mismatch",
                "Version response does not match the selected software type",
            )
        after = await probe(path, context, hash_limit=None)
        if before["sha256"] != after.get("sha256"):
            raise OperationError("software_changed", "Executable changed during verification")
        return SoftwareSpec(
            record.id, kind, str(path), before["sha256"], version, utc_now().isoformat(), True
        )

    async def run(self, cfg, args, context, *, cwd=".", timeout=120, outputs=()):
        if not cfg.enabled:
            raise OperationError("software_disabled", "Software is not enabled")
        if len(outputs) > 20 or len(args) > 200 or any("\0" in arg for arg in args):
            raise OperationError(
                "software_argument_limit", "At most 20 outputs and 200 arguments; no NUL"
            )
        workspace, location = context.grant.workspace, context.location
        scope = context.grant.file_access_scope
        directory = resolve_resource(workspace, location, cwd, directory=True,
                                     file_access_scope=scope)
        current = await probe(Path(cfg.executable), context, hash_limit=None)
        if current.get("sha256") != cfg.sha256:
            raise OperationError(
                "software_changed",
                "Executable missing or changed; run agenthub software verify ID --revision N",
            )
        argv = [cfg.executable, *args]
        env = None
        if cfg.kind == "git":
            argv.insert(1, "--no-pager")
            env = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
        result = await self._observed_run(argv, directory, context, timeout=timeout,
                                          outputs=outputs, env=env)
        result.update(software_id=cfg.resource_id, software_sha256=cfg.sha256,
                      software_version=cfg.version)
        return result

    async def run_native(self, executable, args, context, *, cwd=".", timeout=120, outputs=()):
        if context.grant.execution_mode != "current_user" or context.grant.file_access_scope != "user":
            raise OperationError("software_incompatible", "Native commands require user file scope")
        if len(outputs) > 20 or len(args) > 200 or any("\0" in arg for arg in args):
            raise OperationError("software_argument_limit", "At most 20 outputs and 200 arguments; no NUL")
        directory = resolve_resource(context.grant.workspace, context.location, cwd,
                                     directory=True, file_access_scope="user")
        selected = native_executable(executable, directory)
        argv = [selected, *args]
        env = None
        if Path(selected).stem.casefold() == "git":
            argv.insert(1, "--no-pager")
            env = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
        return await self._observed_run(argv, directory, context, timeout=timeout,
                                        outputs=outputs, env=env)

    async def _observed_run(self, argv, directory, context, *, timeout, outputs, env=None):
        workspace, location = context.grant.workspace, context.location
        scope = context.grant.file_access_scope
        targets = [resolve_resource(workspace, location, str(directory / p),
                                    file_access_scope=scope) for p in outputs]
        before = [await probe(p, context) for p in targets]
        result = await self.driver.run(argv, directory, timeout=timeout, context=context, env=env)
        result["outputs"] = []
        for path, previous in zip(targets, before):
            # Re-resolve after execution: a process may have replaced a parent with a junction.
            try:
                actual = resolve_resource(workspace, location, str(path), file_access_scope=scope)
                after = await probe(actual, context)
                item = {"path": str(actual), "before": previous, "after": after}
            except (OSError, OperationError) as exc:
                item = {
                    "path": str(path),
                    "before": previous,
                    "after": {"observation_status": "unknown", "error": type(exc).__name__},
                }
            result["outputs"].append(item)
        context.check()
        result.update(
            executable=argv[0],
            args=argv[1:],
            execution={
                "cwd": str(directory),
                "default_cwd": str(location.cwd),
                "workspace_version": location.version,
                "executable": argv[0],
            },
            notice="Output observations do not certify correctness or who created a file",
        )
        return result


async def index_directory(
    files, directory, catalog, access, context, *, max_entries=10000, include_ignored=False
):
    """Explicit metadata inventory; traversal/ignore behavior is shared with file discovery."""
    if not 1 <= max_entries <= 1000000:
        raise OperationError("index_limit", "max_entries must be between 1 and 1000000")
    from agent_contracts.resources import ResourceSource

    policy = await files._policy(directory, include_ignored)
    count, errors, partial, last = 0, [], False, None
    source = ResourceSource("index", operation_id=context.operation_id)
    try:
        async for path, _ in files._walk(directory, policy, directories=True):
            check(context)
            if count == max_entries:
                partial = True
                break
            try:
                facts = await probe(path, context, metadata_only=True)
                catalog.observe(access, str(path), facts, source)
                count += 1
                last = str(path)
            except OSError as exc:
                partial = True
                if len(errors) < 10:
                    errors.append({"path": str(path), "error": type(exc).__name__})
    except OperationError as exc:
        if exc.code != "operation_timeout":
            raise
        partial = True
        errors.append({"error": "operation_timeout"})
    return {
        "indexed": count,
        "partial": partial,
        "scope": str(directory),
        "last_observed": last,
        "errors": errors,
        "operation_id": context.operation_id,
        "discovery": policy.diagnostics(True),
        "notice": "Metadata only; ignored directories and links are not traversed",
    }
