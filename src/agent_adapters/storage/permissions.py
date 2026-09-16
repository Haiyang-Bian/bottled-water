"""Transactional local permission authority; ACL/IPC never run inside SQL transactions."""

import json
import os
from dataclasses import replace
from uuid import uuid4

from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.harness import ExecutionStopped
from agent_contracts.permissions import StandingPermissionPolicy
from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import utc_now
from agent_subsystems.workspaces.permission_records import (
    document, policy_from_dict, snapshot_from_dict,
)
from agent_subsystems.workspaces.permissions import freeze_policy, still_allowed


class PermissionBusyError(OperationError):
    def __init__(self, message):
        super().__init__("permission_busy", message)


class SQLitePermissions:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def identity(self):
        self.store._check_environment()
        if self.store.environment is None:
            raise ConfigurationError("权限尚未启用；请运行 agenthub state upgrade。")
        return self.store.environment

    def load(self):
        environment = self.identity()
        row = None if self.store.schema_version < 6 else self.db.execute(
            "SELECT body FROM permission_policies WHERE agent=?", (environment.default_agent_id,)
        ).fetchone()
        value = policy_from_dict(json.loads(row[0])) if row else StandingPermissionPolicy(
            environment.environment_id, environment.default_agent_id, 0,
        )
        if (value.environment_id, value.agent_id) != (
            environment.environment_id, environment.default_agent_id
        ):
            raise ConfigurationError("Permission policy identity mismatch")
        return value

    def _writable(self):
        if self.store.schema_version < 6:
            raise ConfigurationError("agenthub state upgrade is required")
        self.identity()

    def audit(self, operation, body):
        self.db.execute("INSERT INTO permission_events(created,operation,body) VALUES(?,?,?)", (
            utc_now().isoformat(), operation, self.store.redactor.dumps(body),
        ))

    def pending(self):
        return self.db.execute(
            "SELECT * FROM permission_transitions WHERE agent=? "
            "AND state IN ('freezing','retiring','repair_required')",
            (self.identity().default_agent_id,),
        ).fetchone()

    def begin(self, target, expected_revision):
        self._writable()
        with self.store.transaction():
            current = self.load()
            if current.revision != expected_revision:
                raise ContextConflictError("权限修订已变化，请重新读取后提交。")
            if (target.environment_id, target.agent_id) != (current.environment_id, current.agent_id):
                raise ConfigurationError("Permission policy identity mismatch")
            target = replace(target, revision=current.revision + 1)
            # Disabled policies must still have a representable layout when enabled later.
            freeze_policy(replace(target, enabled=True))
            if self.pending():
                raise PermissionBusyError("权限转换尚未完成；请查看 permissions 或 sandbox repair。")
            identifier = uuid4().hex
            manager = {}
            if os.name == "nt":
                from agent_adapters.local.windows_permission_ipc import process_identity
                manager = process_identity()
            self.db.execute("INSERT INTO permission_transitions VALUES(?,?,?,?,?,'freezing')", (
                identifier, current.agent_id, expected_revision, document(target),
                json.dumps({"manager": manager}),
            ))
            affected = []
            for row in self.db.execute(
                "SELECT * FROM permission_preparations WHERE agent=? AND state!='retired'",
                (current.agent_id,),
            ):
                if not still_allowed(snapshot_from_dict(json.loads(row["snapshot"])), target):
                    affected.append(dict(row))
            self.audit("transition.started", {"id": identifier, "revision": target.revision})
        return identifier, target, affected

    def transition(self, identifier, state, details=None):
        with self.store.transaction():
            row = self.pending()
            if row is None or row["id"] != identifier:
                raise ContextConflictError("Permission transition changed")
            self.db.execute("UPDATE permission_transitions SET state=?,body=? WHERE id=?", (
                state, json.dumps({**json.loads(row["body"]), **(details or {})}), identifier,
            ))
            self.audit("transition." + state, {"id": identifier, **(details or {})})

    def commit(self, identifier):
        with self.store.transaction():
            row = self.pending()
            if row is None or row["id"] != identifier or row["state"] != "retiring":
                raise ContextConflictError("Permission transition is not ready")
            target = policy_from_dict(json.loads(row["target"]))
            if self.load().revision != row["expected_revision"]:
                raise ContextConflictError("Permission policy changed during transition")
            for item in self.db.execute(
                "SELECT snapshot,state FROM permission_preparations WHERE agent=?",
                (target.agent_id,),
            ):
                if item["state"] != "retired" and not still_allowed(
                    snapshot_from_dict(json.loads(item["snapshot"])), target
                ):
                    raise OperationError("permission_repair_required", "旧权限尚未确认清理。")
            self.db.execute("INSERT OR REPLACE INTO permission_policies VALUES(?,?,?)", (
                target.agent_id, target.revision, document(target),
            ))
            self.db.execute("UPDATE permission_transitions SET state='completed' WHERE id=?",
                            (identifier,))
            self.audit("policy.changed", {"revision": target.revision, "transition": identifier})
        return target

    def host(self, identifier, body, state="open"):
        with self.store.transaction():
            self.db.execute("INSERT INTO permission_hosts VALUES(?,?,?,?)", (
                identifier, self.identity().default_agent_id, json.dumps(body), state,
            ))

    def host_record(self, identifier):
        self.identity()
        row = self.db.execute("SELECT * FROM permission_hosts WHERE id=? AND agent=?", (
            identifier, self.identity().default_agent_id,
        )).fetchone()
        if row is None:
            raise ConfigurationError("Unknown permission host")
        return {**dict(row), "body": json.loads(row["body"])}

    def save_preparation(self, identifier, host, snapshot, body, state):
        with self.store.transaction():
            self.host_record(host)
            current = self.load()
            if state == "preparing" and (
                self.pending() or not still_allowed(snapshot, current)
            ):
                raise PermissionBusyError("权限正在转换或已改变，请稍后重试。")
            row = self.db.execute("SELECT host,snapshot,state FROM permission_preparations WHERE id=?",
                                  (identifier,)).fetchone()
            if row and row[0] != host:
                raise ConfigurationError("Preparation owner mismatch")
            if row and row["snapshot"] != document(snapshot):
                raise ConfigurationError("Preparation snapshot is immutable")
            if row and row["state"] == "retired" and state != "retired":
                raise OperationError("permission_generation_used", "Retired identity cannot reopen")
            self.db.execute("INSERT INTO permission_preparations VALUES(?,?,?,?,?,?) "
                            "ON CONFLICT(id) DO UPDATE SET body=excluded.body,state=excluded.state", (
                identifier, host, current.agent_id, document(snapshot), json.dumps(body), state,
            ))

    def preparations(self, host=None):
        agent = self.identity().default_agent_id
        where, args = "agent=? AND state!='retired'", [agent]
        if host:
            where += " AND host=?"
            args.append(host)
        return [dict(row) for row in self.db.execute(
            "SELECT * FROM permission_preparations WHERE " + where, args,
        )]

    def register(self, run, host, preparation, snapshot):
        with self.store.transaction():
            if self.pending():
                raise PermissionBusyError("权限正在转换，不能启动新的受限 Run。")
            if not still_allowed(snapshot, self.load()):
                raise ExecutionStopped("permission_revoked")
            row = self.db.execute("SELECT state,host,snapshot FROM permission_preparations WHERE id=?",
                                  (preparation,)).fetchone()
            if (row is None or (row["state"], row["host"]) != ("prepared", host)
                    or row["snapshot"] != document(snapshot)
                    or self.host_record(host)["state"] != "open"):
                raise ExecutionStopped("permission_preparation_invalid")
            self.db.execute("INSERT INTO permission_leases VALUES(?,?,?,?, 'active')", (
                run, host, preparation, document(snapshot),
            ))

    def check_completion(self, run, metadata):
        # Called inside SQLiteStore.try_complete's transaction, before any Context write.
        if not metadata.get("permission_managed"):
            return
        row = self.db.execute("SELECT * FROM permission_leases WHERE run=?", (run,)).fetchone()
        if row is None or row["state"] != "active":
            raise ExecutionStopped("permission_lease_invalid")
        prepared = self.db.execute("SELECT state,host,snapshot FROM permission_preparations WHERE id=?",
                                   (row["preparation"],)).fetchone()
        if (prepared is None or prepared["state"] != "prepared"
                or prepared["host"] != row["host"] or prepared["snapshot"] != row["snapshot"]
                or self.host_record(row["host"])["state"] != "open"):
            raise ExecutionStopped("permission_lease_invalid")
        if not still_allowed(snapshot_from_dict(json.loads(row["snapshot"])), self.load()):
            raise ExecutionStopped("permission_revoked")

    def finish(self, run, *, cleaned):
        with self.store.transaction():
            self.db.execute("UPDATE permission_leases SET state=? WHERE run=?", (
                "closed" if cleaned else "repair_required", run,
            ))

    def busy(self, preparations):
        return [dict(row) for row in self.db.execute(
            "SELECT run,preparation,state FROM permission_leases WHERE state!='closed'"
        ) if row["preparation"] in preparations]
