"""v6 authority and completion are atomic; old trust never becomes permission."""

import json
import sqlite3
from dataclasses import replace

import pytest

from agent_adapters.storage.permissions import SQLitePermissions, PermissionBusyError
from agent_adapters.storage.session_lock import SessionLock
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.harness import ExecutionStopped
from agent_contracts.permissions import PathPermission
from agent_runtime.core.ports import ContextConflictError
from agent_subsystems.workspaces.permissions import freeze_policy


def enabled(authority, root):
    return replace(authority.load(), enabled=True, grants=(PathPermission(root, "modify"),))


def publish(authority, target):
    identifier, _, affected = authority.begin(target, authority.load().revision)
    assert not affected
    authority.transition(identifier, "retiring")
    return authority.commit(identifier)


def test_policy_cas_transition_and_expansion(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    try:
        authority = SQLitePermissions(store)
        store.trust(tmp_path)
        assert authority.load().revision == 0 and not authority.load().enabled
        current = publish(authority, enabled(authority, tmp_path))
        assert current.revision == 1
        with pytest.raises(ContextConflictError):
            authority.begin(current, 0)
        identifier, _, _ = authority.begin(replace(current, enabled=False), 1)
        with pytest.raises(PermissionBusyError):
            authority.begin(current, 1)
        authority.transition(identifier, "aborted", {"reason": "busy"})
        assert authority.load() == current
    finally:
        store.close()


def test_unclean_preparation_prevents_withdrawal_and_completion(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    try:
        authority = SQLitePermissions(store)
        current = publish(authority, enabled(authority, tmp_path))
        snapshot = freeze_policy(current)
        authority.host("host", {"pid": 123, "created": "test"})
        authority.save_preparation("prep", "host", snapshot, {}, "prepared")
        authority.register("run", "host", "prep", snapshot)
        with store.transaction():
            authority.check_completion("run", {"permission_managed": True})
        identifier, _, affected = authority.begin(replace(current, enabled=False), 1)
        assert [a["id"] for a in affected] == ["prep"]
        assert authority.busy({"prep"})[0]["run"] == "run"
        authority.transition(identifier, "retiring")
        with pytest.raises(Exception, match="旧权限"):
            authority.commit(identifier)
        assert authority.load() == current
        authority.finish("run", cleaned=True)
        authority.save_preparation("prep", "host", snapshot, {}, "retired")
        authority.commit(identifier)
        with pytest.raises(ExecutionStopped, match="permission_lease_invalid"):
            authority.check_completion("run", {"permission_managed": True})
    finally:
        store.close()


def test_task_mode_and_workspace_update_share_revision(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    try:
        session = store.new_session(tmp_path)
        assert session["execution_mode"] == "current_user"
        selection = {"mode": "custom", "roots": [{"path": str(tmp_path), "access": "read"}]}
        with SessionLock(tmp_path / "locks", session["id"]) as lock:
            updated = store.update_workspace(
                session["id"], cwd=tmp_path, granted_roots=session["granted_roots"],
                expected_version=0, lock=lock, execution_mode="windows_lpac",
                permission_selection=selection,
            )
            assert updated["workspace_version"] == 1
            assert updated["execution_mode"] == "windows_lpac"
            with pytest.raises(ContextConflictError):
                store.update_workspace(session["id"], cwd=tmp_path, granted_roots=[],
                                       expected_version=0, lock=lock)
        assert store.db.execute("SELECT count(*) FROM session_events").fetchone()[0] == 1
        assert store.db.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
    finally:
        store.close()


@pytest.mark.parametrize("version", [3, 4, 5])
def test_direct_upgrade_keeps_identity_and_never_imports_trust(tmp_path, version):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStore(path)
    environment = store.environment
    session = store.new_session(tmp_path)
    store.trust(tmp_path)
    store.close()
    with sqlite3.connect(path) as db:
        # Owned fixture only: construct a historical schema without changing real state.
        for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
            name = row[0]
            if (name.startswith("permission_") or name == "task_permissions"
                    or version < 5 and (name.startswith("resource") or name == "software")
                    or version < 4 and name.startswith("memor")):
                db.execute('DROP TABLE "' + name + '"')
        db.execute(f"PRAGMA user_version={version}")
    reader = SQLiteStore(path, readonly=True)
    assert reader.session(session["id"])["execution_mode"] == "current_user"
    assert not SQLitePermissions(reader).load().enabled
    reader.close()
    store = SQLiteStore(path)
    try:
        assert store.schema_version == 6 and store.environment == environment
        assert store.is_trusted(tmp_path)
        assert not SQLitePermissions(store).load().enabled
        assert store.session(session["id"])["execution_mode"] == "current_user"
        assert store.db.execute("SELECT count(*) FROM permission_events").fetchone()[0] == 0
        with sqlite3.connect(store.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == version
    finally:
        store.close()


def test_foreign_snapshot_is_not_read_or_registered(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    try:
        authority = SQLitePermissions(store)
        policy = publish(authority, enabled(authority, tmp_path))
        body = json.loads(store.db.execute("SELECT body FROM permission_policies").fetchone()[0])
        body["environment_id"] = "another-environment"
        store.db.execute("UPDATE permission_policies SET body=?", (json.dumps(body),))
        with pytest.raises(Exception, match="identity mismatch"):
            authority.load()
        assert policy.environment_id != body["environment_id"]
    finally:
        store.close()


@pytest.mark.parametrize("version", [1, 2, 3, 4, 5])
def test_v6_failure_rolls_back_all_changes_and_keeps_wal_backup(tmp_path, monkeypatch, version):
    from agent_adapters.storage import migration
    from test_local_environment import legacy

    path = tmp_path / "state.sqlite3"
    if version < 3:
        legacy(path, version)
    else:
        store = SQLiteStore(path)
        store.new_session(tmp_path)
        store.close()
        with sqlite3.connect(path) as db:
            for (name,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                if (name.startswith("permission_") or name == "task_permissions"
                        or version < 5 and (name.startswith("resource") or name == "software")
                        or version < 4 and name.startswith("memor")):
                    db.execute('DROP TABLE "' + name + '"')
            db.execute(f"PRAGMA user_version={version}")
    schema = migration.PERMISSION_SCHEMA
    # Fail after every new table has been created, before the v6 commit.
    monkeypatch.setattr(migration, "PERMISSION_SCHEMA", (*schema, "INVALID SQL"))
    with sqlite3.connect(path) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("INSERT INTO trusted VALUES('wal-only-v6','now')")
        writer.commit()
        before = list(writer.iterdump())
        with pytest.raises(sqlite3.OperationalError):
            SQLiteStore(path)
        assert writer.execute("PRAGMA user_version").fetchone()[0] == version
        assert list(writer.iterdump()) == before
        backups = list(tmp_path.glob("*.bak"))
        assert len(backups) == 1
        with sqlite3.connect(backups[0]) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == version
            assert backup.execute("SELECT path FROM trusted WHERE path='wal-only-v6'").fetchone()
        monkeypatch.setattr(migration, "PERMISSION_SCHEMA", schema)
        upgraded = SQLiteStore(path)
        try:
            assert upgraded.schema_version == 6
            assert not SQLitePermissions(upgraded).load().enabled
            assert upgraded.db.execute("SELECT count(*) FROM permission_events").fetchone()[0] == 0
        finally:
            upgraded.close()
