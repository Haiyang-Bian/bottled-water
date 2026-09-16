"""Explicitly injected LPAC transport. No default CLI activation or token fallback."""

import asyncio
import json
import math
import os
from pathlib import Path
import threading
import time
from uuid import uuid4

from agent_contracts.errors import OperationError
from agent_contracts.harness import ExecutionStopped
from agent_subsystems.workspaces.permissions import authorize_path, inheritable_roots
from .dependencies import DependencyManifest
from .windows_jobs import OwnedJob
from .windows_lpac import LpacProfile
from .windows_run_acl import RunAcl
from .worker_protocol import REQUEST_LIMIT, RESPONSE_LIMIT, VERSION, encode, response_result


class RestrictedAuthorization:
    def authorize(self, request):
        context = request.context
        if context.grant.execution_mode != "windows_lpac" or context.grant.policy is None:
            raise ExecutionStopped("isolation_policy_mismatch")
        if request.spec.capability not in context.grant.capabilities:
            return "deny"
        if request.target is not None:
            value = Path(request.target)
            if value.drive and not value.is_absolute():
                raise OperationError("ambiguous_path", "Drive-relative paths are not accepted")
            if not value.is_absolute():
                value = context.location.cwd / value
            # Pure lexical check; the worker resolves aliases and the OS enforces real objects.
            value = Path(os.path.normcase(os.path.normpath(str(value))))
            if not authorize_path(context.grant.policy, value, request.operation or "read").allowed:
                raise OperationError("permission_denied",
                    f"Frozen policy denies {request.operation or 'read'} at {value}. "
                    f"Current cwd is {context.location.cwd}; its parent is not implicitly authorized.")
        return "allow"


class WindowsRestrictedDriver:
    capabilities = {"filesystem_isolation": True, "network_isolation": True,
                    "process_tree_control": True}

    def __init__(self, prepared, bundle, private_parent, executables, *, namespace_experiment=None):
        self.prepared, self.bundle = prepared, Path(bundle)
        self.manifest = DependencyManifest.capture(self.bundle)
        if self.manifest.digest != prepared.key[3]:
            raise OperationError("dependency_changed", "Preparation belongs to another dependency bundle")
        self.private = Path(private_parent) / ("run-" + uuid4().hex)
        self.executables = dict(executables)
        self.namespace_experiment = namespace_experiment
        entries = json.loads(self.manifest.encoded)
        self.software = {name: {"path": path, "sha256": entries[
            str(Path(path).relative_to(self.bundle))]["sha256"]}
            for name, path in self.executables.items()}
        self.profile, self.job, self.acl, self.permission_lease = None, None, None, None
        self.context, self.invalid_reason = None, None
        self.active, self.stop = set(), threading.Event()
        self.lock = asyncio.Lock()
        self.closed = False
        self.audit = {}

    def record(self, state, grants=None):
        self.audit.update(state=state)
        if grants is not None:
            self.audit["grants"] = grants
        path = self.private.parent / (self.private.name + "-acl.json")
        pending = path.with_suffix(".pending")
        pending.write_text(json.dumps(self.audit), encoding="utf-8")
        os.replace(pending, path)

    async def prepare(self, context):
        if self.context is not None:
            if context.run_id != self.context.run_id:
                raise ExecutionStopped("isolation_run_mismatch")
            self.check()
            return
        if context.grant.policy != self.prepared.snapshot:
            raise ExecutionStopped("isolation_policy_mismatch")
        if context.agent_id != self.prepared.snapshot.policy.agent_id:
            raise ExecutionStopped("isolation_identity_mismatch")
        self.manifest.verify()
        self.private.mkdir()  # Host-selected root and fresh UUID; never a model path.
        self.context = context
        self.permission_lease = self.prepared.acquire(context.run_id)
        self.profile = LpacProfile()
        self.audit = {"profile_name": self.profile.name, "run_id": context.run_id,
                      "host_pid": os.getpid(), "private": str(self.private), "grants": []}
        self.record("creating_profile")
        self.profile.create()
        self.audit["sid"] = self.profile.sid
        self.record("profile_created")
        self.job = OwnedJob()

        def journal(grants):
            # Trusted host-owned audit, outside the worker's private directory.
            self.record("acl_pending", grants)

        self.acl = RunAcl(self.profile.sid, journal)
        self.acl.grant(self.bundle, 0x1200A9)
        self.acl.grant(self.private, 0x1301BF)
        self.record("ready")

    def check(self):
        if self.invalid_reason or self.closed:
            raise ExecutionStopped(self.invalid_reason or "permission_lease_invalid")
        if self.permission_lease:
            try:
                self.permission_lease.require_valid()
            except OperationError as exc:
                raise ExecutionStopped(exc.code) from exc

    def environment(self):
        system = Path(os.environ["SystemRoot"])
        result = {"SystemRoot": str(system), "WINDIR": str(system), "SystemDrive": system.drive,
                  "PATH": os.pathsep.join([str(Path(p).parent) for p in self.executables.values()]),
                  "PATHEXT": ".COM;.EXE;.BAT;.CMD",
                  "TEMP": str(self.private), "TMP": str(self.private), "USERPROFILE": str(self.private),
                  "LOCALAPPDATA": str(self.private), "APPDATA": str(self.private),
                  "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                  "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL", "GIT_TERMINAL_PROMPT": "0",
                  "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "credential.helper",
                  "GIT_CONFIG_VALUE_0": "", "GIT_EDITOR": "true", "GIT_OPTIONAL_LOCKS": "0",
                  "UV_CACHE_DIR": str(self.private / "uv-cache"), "UV_OFFLINE": "1",
                  "UV_NO_CONFIG": "1", "UV_NO_MANAGED_PYTHON": "1",
                  "UV_PYTHON": self.executables["python"],
                  "UV_PYTHON_DOWNLOADS": "never", "DOTNET_CLI_HOME": str(self.private),
                  "POWERSHELL_TELEMETRY_OPTOUT": "1", "LC_ALL": "C"}
        return result

    async def invoke(self, operation, parameters, context, *, timeout=120):
        context.check()
        async with self.lock:
            await self.prepare(context)
            self.check()
            remaining = min(timeout, context.deadline - time.monotonic())
            if not math.isfinite(remaining) or remaining <= 0:
                raise OperationError("process_timeout", "Command deadline expired")
            request_id = uuid4().hex
            message = {"version": VERSION, "request_id": request_id, "operation": operation,
                       "parameters": parameters, "config": {
                           "cwd": str(context.location.cwd), "location_version": context.location.version,
                           "permissions": [{"path": str(r.path), "access": r.access.value}
                                           for r in inheritable_roots(context.grant.policy)],
                           "timeout": remaining, "executables": self.software}}
            try:
                payload = encode(message, REQUEST_LIMIT)
            except ValueError as exc:
                raise OperationError("request_too_large", str(exc)) from exc
            task = asyncio.create_task(asyncio.to_thread(
                self.profile.run,
                [self.executables["python"], "-I", "-S", "-B", str(self.bundle / "worker.py")],
                self.private, environment=self.environment(), timeout=remaining,
                registry_read=True, instrumentation="pwsh" in self.executables,
                namespace_experiment=self.namespace_experiment,
                policy_experiment=self.prepared.generation, parent_jobs=(self.job.handle,),
                cancel_event=self.stop, stdin_data=payload, output_limit=RESPONSE_LIMIT + 4,
                binary_output=True))
            self.active.add(task)
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                self.stop.set()
                await asyncio.shield(task)
                raise
            except Exception as exc:
                raise ExecutionStopped("isolation_execution_failed") from exc
            finally:
                self.active.discard(task)
            self.check()
            context.check()
            if not result["job_drained"] or self.profile.cleanup_blocked:
                raise ExecutionStopped("isolation_cleanup_unconfirmed")
            if result["timed_out"]:
                raise OperationError("process_timeout", "Command group exceeded its deadline")
            if result["cancelled"]:
                raise ExecutionStopped("permission_lease_invalid")
            if result["exit_code"] != 0 or result["truncated"] or result["pipe_errors"]:
                raise ExecutionStopped("worker_protocol_error")
            try:
                response = response_result(result["stdout"], request_id)
            except ValueError as exc:
                raise ExecutionStopped("worker_protocol_error") from exc
            if not response["ok"]:
                raise OperationError(response["error_code"], response["error"])
            return response["result"]

    async def run(self, argv, cwd, *, timeout, context, env=None):
        # Allowed executable mapping and actual cwd are checked again inside LPAC.
        return await self.invoke("command", {"argv": argv, "cwd": str(cwd)}, context, timeout=timeout)

    async def drain(self):
        self.stop.set()
        if self.active:
            await asyncio.gather(*self.active, return_exceptions=True)
        if self.job:
            try:
                self.job.close()
            except Exception as exc:
                raise ExecutionStopped("isolation_cleanup_unconfirmed") from exc
        if self.profile and self.profile.cleanup_blocked:
            raise ExecutionStopped("isolation_cleanup_unconfirmed")
        self.check()

    async def revoke(self, reason_code):
        self.invalid_reason = reason_code
        self.stop.set()
        if self.job and self.job.handle:
            self.job.terminate()

    async def wait_invalid(self):
        while True:
            try:
                self.check()
            except ExecutionStopped as exc:
                return exc.reason_code
            await asyncio.sleep(0.1)

    async def aclose(self):
        if self.closed:
            return
        self.stop.set()
        try:
            if self.active:
                await asyncio.gather(*self.active, return_exceptions=True)
            if self.job:
                self.job.close()
            if self.profile and self.profile.cleanup_blocked:
                raise ExecutionStopped("isolation_cleanup_unconfirmed")
            if self.permission_lease:
                self.permission_lease.finish(job_drained=True)
                self.permission_lease = None
            if self.acl:
                self.acl.close()
            if self.profile:
                self.profile.delete()
                self.profile = None
            if self.audit:
                self.record("closed")
            self.closed = True
        except BaseException:
            if self.audit:
                self.record("repair_required")
            raise
