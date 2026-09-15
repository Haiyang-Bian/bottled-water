"""Bounded native resource probes and software calls using the existing process driver."""

import asyncio
import hashlib
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


def check(context):
    context.check()
    if time.monotonic() >= context.deadline:
        raise OperationError("operation_timeout", "Resource operation deadline expired")


async def probe(path, context, *, hash_limit=64 * 1024 * 1024, metadata_only=False):
    check(context)
    path = Path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False, "observation_status": "observed"}
    facts = {
        "exists": True,
        "kind": "directory" if path.is_dir() else "file",
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "observation_status": "observed",
        "hash_status": "not_requested",
    }
    if facts["kind"] == "file" and not metadata_only:
        if hash_limit is not None and stat.st_size > hash_limit:
            facts["hash_status"] = "size_limit"
        else:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(256 * 1024):
                    check(context)
                    digest.update(block)
                    await asyncio.sleep(0)
            latest = path.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (
                latest.st_size,
                latest.st_mtime_ns,
                latest.st_ino,
            ):
                raise OperationError("resource_changed", "File changed during verification; retry")
            facts.update(sha256=digest.hexdigest(), hash_status="complete")
    check(context)
    return facts


def discover(kind, cwd, explicit=None):
    if kind not in {"python", "uv", "git"}:
        raise OperationError("software_kind", "Supported software: python, uv, git")
    candidates = []
    if explicit:
        value = Path(explicit).expanduser()
        candidates.append(value if value.is_absolute() else Path(cwd) / value)
    else:
        if kind == "python":
            candidates.extend(
                [
                    Path(cwd)
                    / ".venv"
                    / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
                    Path(sys.executable),
                ]
            )
        for directory in os.get_exec_path():
            found = shutil.which(kind, path=directory)
            if found:
                candidates.append(Path(found))
    result, seen = [], set()
    for path in candidates:
        absolute = os.path.normcase(str(path.resolve()))
        if absolute in seen or not path.exists():
            continue
        seen.add(absolute)
        placeholder = "\\microsoft\\windowsapps\\" in absolute.lower().replace("/", "\\")
        result.append(
            {
                "kind": kind,
                "path": absolute,
                "verified": False,
                "notice": "WindowsApps alias: choose a real executable"
                if placeholder
                else "Requires verification",
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
        directory = resolve_resource(workspace, location, cwd, directory=True)
        current = await probe(Path(cfg.executable), context, hash_limit=None)
        if current.get("sha256") != cfg.sha256:
            raise OperationError(
                "software_changed",
                "Executable missing or changed; run agenthub software verify ID --revision N",
            )
        targets = [resolve_resource(workspace, location, str(directory / p)) for p in outputs]
        before = [await probe(p, context) for p in targets]
        argv = [cfg.executable, *args]
        env = None
        if cfg.kind == "git":
            argv.insert(1, "--no-pager")
            env = {"GIT_TERMINAL_PROMPT": "0", "GIT_EDITOR": "true"}
        result = await self.driver.run(argv, directory, timeout=timeout, context=context, env=env)
        result["outputs"] = []
        for path, previous in zip(targets, before):
            # Re-resolve after execution: a process may have replaced a parent with a junction.
            try:
                actual = resolve_resource(workspace, location, str(path))
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
            software_id=cfg.resource_id,
            software_sha256=cfg.sha256,
            software_version=cfg.version,
            execution={
                "cwd": str(directory),
                "default_cwd": str(location.cwd),
                "workspace_version": location.version,
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
