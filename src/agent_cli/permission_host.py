"""One CLI's prepared policies and fixed control messages; no background service."""

import asyncio
import json
from uuid import uuid4

from agent_adapters.storage.permissions import SQLitePermissions, PermissionBusyError
from agent_adapters.storage.session_lock import SessionLock, SessionBusyError
from agent_contracts.errors import OperationError
from agent_subsystems.workspaces.permission_records import snapshot_from_dict, policy_from_dict
from agent_subsystems.workspaces.permissions import still_allowed
from agent_subsystems.workspaces.permission_preparation import PreparedPolicy


class PermissionHost:
    def __init__(self, store):
        self.store = store
        self.authority = SQLitePermissions(store)
        self.id = uuid4().hex
        self.instances = {}
        self.frozen = None
        self.running = False
        self.active_preparation = None
        self.server = None
        self.lock = None

    def start(self):
        from agent_adapters.local.windows_permission_ipc import PermissionControlServer
        self.loop = asyncio.get_running_loop()
        self.lock = SessionLock(self.store.path.parent / "locks", "host:" + self.id)
        self.lock.__enter__()

        def dispatch(request, peer):
            future = asyncio.run_coroutine_threadsafe(self.control(request, peer), self.loop)
            return future.result(timeout=120 if request["operation"] == "retire" else 5)

        self.server = PermissionControlServer(
            self.store.environment.environment_id, self.id, dispatch,
        )
        try:
            body = self.server.start()
            self.authority.host(self.id, body)
        except BaseException:
            self.server.close()
            self.lock.__exit__()
            raise

    async def control(self, request, peer):
        operation, identifier = request["operation"], request["transition"]
        if operation == "status":
            return {"running": self.running, "frozen": self.frozen}
        row = self.store.db.execute("SELECT * FROM permission_transitions WHERE id=?",
                                    (identifier,)).fetchone()
        if row is None or row["agent"] != self.store.environment.default_agent_id:
            raise OperationError("permission_control_denied", "Unknown transition")
        if peer is not None and peer != json.loads(row["body"]).get("manager"):
            raise OperationError("permission_control_denied", "Transition initiator mismatch")
        if operation == "thaw":
            if row["state"] not in {"completed", "aborted", "repair_required"}:
                raise OperationError("permission_control_denied", "Transition is still active")
            if self.frozen == identifier:
                self.frozen = None
            return {"thawed": True}
        if row["state"] not in {"freezing", "retiring"}:
            raise OperationError("permission_control_denied", "Stale transition")
        target = policy_from_dict(json.loads(row["target"]))
        affected = [item for item in self.instances.values()
                    if not still_allowed(item.snapshot, target)]
        busy = self.authority.busy({item.generation for item in affected})
        starting = self.running and any(item.generation == self.active_preparation for item in affected)
        if starting or busy:
            detail = ", ".join(r["run"] for r in busy) or "CLI 正在启动或清理执行"
            raise PermissionBusyError("请先取消受影响 Run 或等待清理完成：" + detail)
        if operation == "freeze":
            if self.frozen not in {None, identifier}:
                raise PermissionBusyError("Host already frozen")
            self.frozen = identifier
            return {"frozen": True}
        if operation == "retire":
            if self.frozen != identifier or row["state"] != "retiring":
                raise OperationError("permission_control_denied", "Freeze is required")
            for prepared in affected:
                prepared.retire()
                self.instances.pop(prepared.key, None)
            return {"retired": [item.generation for item in affected]}
        raise OperationError("permission_control_denied", "Unknown control operation")

    def prepare(self, snapshot, manifest, progress=None):
        from agent_adapters.local.windows_permission_backend import WindowsPermissionBackend
        if self.frozen and not self.authority.pending():
            self.frozen = None  # An acknowledgement may have been lost after an aborted conversion.
        if self.frozen or self.authority.pending():
            raise PermissionBusyError("权限正在转换，请稍后重试。")
        key = (snapshot.policy.environment_id, snapshot.policy.agent_id,
               snapshot.digest, manifest.digest)
        if key not in self.instances:
            # A dead or failed host must be repaired before we issue a new identity.
            for row in self.authority.preparations():
                if row["state"] == "repair_required":
                    raise OperationError("permission_repair_required", "请先运行 sandbox repair。")
                if row["host"] != self.id:
                    self.require_live_owner(row["host"])
            backend = WindowsPermissionBackend(self.authority, self.id, manifest, progress=progress)
            prepared = PreparedPolicy(snapshot, manifest.digest, backend)
            self.instances[key] = prepared
            try:
                prepared.prepare()
            except BaseException:
                if prepared.state == "retired":
                    self.instances.pop(key, None)
                raise
        return self.instances[key]

    def require_live_owner(self, identifier):
        from agent_adapters.local.windows_permission_ipc import process_identity
        record = self.authority.host_record(identifier)
        body = record["body"]
        try:
            live = process_identity(body["pid"])
            if record["state"] != "open" or live != {
                key: body[key] for key in ("pid", "created", "owner")
            }:
                raise ValueError("Host identity changed")
            lock = SessionLock(self.store.path.parent / "locks", "host:" + identifier)
            try:
                lock.__enter__()
            except SessionBusyError:
                return
            else:
                lock.__exit__()
        except (OSError, ValueError, KeyError) as exc:
            raise OperationError("permission_repair_required",
                                 "旧宿主身份未核实；请先运行 sandbox repair。") from exc
        raise OperationError("permission_repair_required", "旧宿主未持有锁；请运行 sandbox repair。")

    async def remote(self, host, operation, transition):
        from agent_adapters.local.windows_permission_ipc import request_control
        if host == self.id:
            return await self.control({"operation": operation, "transition": transition}, None)
        record = self.authority.host_record(host)
        return await asyncio.to_thread(request_control, record["body"],
                                       self.store.environment.environment_id,
                                       host, operation, transition)

    async def close(self):
        errors = []
        for prepared in self.instances.values():
            try:
                prepared.retire()
            except Exception:
                errors.append(prepared.generation)
        if self.server:
            await asyncio.to_thread(self.server.close)
        if self.lock:
            self.lock.__exit__()
        if self.server:
            with self.store.transaction():
                self.store.db.execute("UPDATE permission_hosts SET state=? WHERE id=?",
                                      ("repair_required" if errors else "closed", self.id))
        if errors:
            raise OperationError("permission_repair_required", "旧权限清理未确认，请运行 sandbox repair。")


async def remote_control(authority, host, operation, transition):
    from agent_adapters.local.windows_permission_ipc import request_control
    record = authority.host_record(host)
    return await asyncio.to_thread(request_control, record["body"],
                                   authority.store.environment.environment_id,
                                   host, operation, transition)


def recover_preparations(store, dependencies):
    """Explicit recovery never assumes a nonresponsive host is dead."""
    from agent_adapters.local.windows_permission_backend import WindowsPermissionBackend
    from agent_adapters.local.windows_permission_ipc import process_identity, ProcessExitedError
    authority = SQLitePermissions(store)
    pending = authority.pending()
    if pending:
        manager = json.loads(pending["body"]).get("manager")
        if not manager:
            raise OperationError("permission_repair_required", "转换发起者身份未保存，无法自动修复。")
        try:
            observed = process_identity(manager["pid"])
        except OSError as exc:
            if not isinstance(exc, ProcessExitedError) and getattr(exc, "winerror", None) != 87:
                raise
        else:
            if observed == manager:
                raise PermissionBusyError("权限转换发起者仍存活，不能接管转换。")
    repaired = []
    for row in authority.preparations():
        host = authority.host_record(row["host"])
        body = host["body"]
        try:
            observed = process_identity(body["pid"])
        except OSError as exc:
            if not isinstance(exc, ProcessExitedError) and getattr(exc, "winerror", None) != 87:
                raise
        else:
            if observed == {key: body[key] for key in ("pid", "created", "owner")}:
                raise PermissionBusyError("原 CLI 仍存活，不能接管其权限清理。")
        with SessionLock(store.path.parent / "locks", "host:" + row["host"]):
            # Every managed Run's audit must prove its named Job no longer exists/has no process.
            from agent_adapters.local.permission_recovery import recover_runs
            recover_runs(store, row["host"], dependencies)
            backend = WindowsPermissionBackend(authority, row["host"], dependencies)
            backend.records[row["id"]] = {
                "snapshot": snapshot_from_dict(json.loads(row["snapshot"])),
                "body": json.loads(row["body"]), "state": row["state"],
            }
            backend.retire(row["id"])
            repaired.append(row["id"])
    pending = authority.pending()
    if pending:
        authority.transition(pending["id"], "aborted", {"repair": "old policy retained"})
    return repaired
