"""Pure in-memory fault tests using the real v6 schema and SQLite authority."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from agent_adapters.storage.migration import migrate, read_environment
from agent_adapters.storage.permissions import PermissionBusyError, SQLitePermissions
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.identity import PlatformIdentity
from agent_contracts.permissions import PathPermission
from agent_runtime.core.ports import ContextConflictError
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.permission_coordination import change_policy
from agent_subsystems.workspaces.permissions import freeze_policy


@pytest.fixture
def store():
    instance = SQLiteStore.__new__(SQLiteStore)
    instance.db = sqlite3.connect(":memory:", isolation_level=None)
    instance.db.row_factory = sqlite3.Row
    instance.path = Path.cwd() / "unused-memory-state.sqlite3"
    instance.identity = PlatformIdentity("test", "test", "test")
    instance.redactor = Redactor()
    instance.backup_path = migrate(instance.db, instance.path, 0, instance.identity)
    instance.schema_version = 6
    instance.environment = read_environment(instance.db, instance.identity)
    yield instance
    instance.close()


async def initialized(store):
    authority = SQLitePermissions(store)
    policy = replace(authority.load(), enabled=True,
                     grants=(PathPermission(Path.cwd(), "modify"),))
    async def no_hosts(*_):
        raise AssertionError("No host should be contacted")
    policy = await change_policy(authority, policy, 0, no_hosts)
    authority.host("host", {"pid": 123})
    snapshot = freeze_policy(policy)
    authority.save_preparation("prep", "host", snapshot, {}, "prepared")
    return authority, policy, snapshot


async def test_busy_transition_keeps_policy_and_unblocks_new_runs(store):
    authority, policy, snapshot = await initialized(store)
    async def control(host, operation, identifier):
        raise PermissionBusyError("running:run")
    with pytest.raises(PermissionBusyError, match="running"):
        await change_policy(authority, replace(policy, enabled=False), 1, control)
    assert authority.load() == policy
    assert authority.pending() is None
    authority.register("run", "host", "prep", snapshot)
    assert authority.busy({"prep"})[0]["run"] == "run"


async def test_idle_ack_cleanup_precedes_policy_commit(store):
    authority, policy, snapshot = await initialized(store)
    calls = []
    async def control(host, operation, identifier):
        calls.append(operation)
        if operation == "retire":
            assert authority.load() == policy
            with pytest.raises(PermissionBusyError):
                authority.register("racing", "host", "prep", snapshot)
            authority.save_preparation("prep", host, snapshot, {}, "retired")
    current = await change_policy(authority, replace(policy, enabled=False), 1, control)
    assert not current.enabled and current.revision == 2
    assert calls == ["freeze", "retire", "thaw"]


async def test_partial_cleanup_failure_never_claims_new_policy(store):
    authority, policy, _ = await initialized(store)
    async def control(host, operation, identifier):
        if operation == "retire":
            raise OSError("ACL unavailable")
    with pytest.raises(OSError):
        await change_policy(authority, replace(policy, enabled=False), 1, control)
    assert authority.load() == policy
    assert authority.pending()["state"] == "repair_required"


async def test_revision_conflict_and_expansion_do_not_contact_active_host(store):
    authority, policy, snapshot = await initialized(store)
    authority.register("run", "host", "prep", snapshot)
    async def control(*_):
        raise AssertionError("Expansion does not retire an existing snapshot")
    with pytest.raises(ContextConflictError):
        await change_policy(authority, policy, 0, control)
    current = await change_policy(authority, policy, 1, control)
    assert current.revision == 2
    with store.transaction():
        authority.check_completion("run", {"permission_managed": True})


async def test_unknown_lease_completion_rolls_back_before_context(store):
    from agent_contracts.harness import ExecutionStopped
    from agent_runtime.core.run_types import (
        ContextDelta, EventEnvelope, RunResult, RunState, Usage, utc_now,
    )
    authority, _, _ = await initialized(store)
    session = store.new_session(Path.cwd())
    store.db.execute("INSERT INTO runs(id,scope,state,created,request) VALUES(?,?,?,?,?)", (
        "missing", session["id"], "running", "now",
        json.dumps({"metadata": {"permission_managed": True}}),
    ))
    # These fields are constructed from the public completion contract.
    result = RunResult(run_id="missing", context_scope_id=session["id"], state=RunState.COMPLETED,
                       reason_code="completed", output="done", started_at=utc_now(),
                       finished_at=utc_now(), usage=Usage())
    event = EventEnvelope(run_id="missing", context_scope_id=session["id"], sequence=1,
                          type="system.run_completed", payload={})
    delta = ContextDelta(expected_version=0, blackboard={}, messages=())
    with pytest.raises(ExecutionStopped, match="permission_lease_invalid"):
        await store.try_complete(delta, result, event)
    assert store.db.execute("SELECT count(*) FROM contexts").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert store.db.execute("SELECT result FROM runs").fetchone()[0] is None
    assert authority.pending() is None
