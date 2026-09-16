"""Local identity and v3 persistence: inspect legacy facts, never invent a new history."""

import json
import sqlite3

import pytest

from agent_adapters.storage.session_lock import SessionBusyError, SessionLock
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.sessions import SessionCatalogReader
from agent_contracts.errors import ConfigurationError
from agent_contracts.identity import PlatformIdentity
from agent_runtime.core.ports import ContextConflictError


def legacy(path, version=2):
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        db.executescript("""
        CREATE TABLE trusted(path TEXT PRIMARY KEY, created TEXT NOT NULL);
        CREATE TABLE sessions(id TEXT PRIMARY KEY, root TEXT, dirs TEXT, created TEXT, updated TEXT);
        CREATE TABLE contexts(scope TEXT PRIMARY KEY, version INTEGER, body TEXT);
        CREATE TABLE runs(id TEXT PRIMARY KEY, scope TEXT, state TEXT, created TEXT,
                          request TEXT, result TEXT, sequence INTEGER DEFAULT 0);
        CREATE TABLE events(id TEXT PRIMARY KEY, run TEXT, sequence INTEGER, body TEXT,
                            UNIQUE(run,sequence));
        """)
        if version >= 2:
            db.execute("CREATE TABLE continuation_metadata(scope TEXT PRIMARY KEY, body TEXT)")
            db.execute("INSERT INTO continuation_metadata VALUES('old','{\"consumed\":1}')")
        db.execute("INSERT INTO sessions VALUES('old',?,'[]','2026-01-01','2099-01-01')",
                   (str(path.parent),))
        db.execute("INSERT INTO trusted VALUES(?,'2026-01-01')", (str(path.parent),))
        db.execute("INSERT INTO runs VALUES('r','old','failed','2026-01-02',?,NULL,0)",
                   (json.dumps({"input": "原有请求"}),))
        db.execute("INSERT INTO contexts VALUES('old',7,?)", (json.dumps({
            "version": 7, "messages": [{"role": "user", "content": "old context"}],
            "blackboard": {}, "agent_memories": {"local": {"agent_id": "local"}},
        }),))
        db.execute(f"PRAGMA user_version={version}")


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_read_does_not_bind_and_atomic_upgrade_keeps_facts(tmp_path, version):
    path = tmp_path / "state.sqlite3"
    legacy(path, version)
    before = path.read_bytes()
    rows = SessionCatalogReader(tmp_path).list()
    assert rows[0].title == "原有请求" and rows[0].environment_id is None
    assert path.read_bytes() == before and not list(tmp_path.glob("*.bak"))
    with SessionLock(tmp_path / "locks", "old"):
        with pytest.raises(SessionBusyError):
            SQLiteStore(path)
    store = SQLiteStore(path)
    try:
        assert store.schema_version == 6
        session = store.session("old")
        assert session["origin_root"] == session["cwd"] == str(tmp_path)
        assert session["granted_roots"] == [str(tmp_path)]
        assert session["workspace_version"] == 0
        assert store.environment.default_agent_id == "local"
        assert store.is_trusted(tmp_path)
        assert store.db.execute("SELECT version FROM contexts").fetchone()[0] == 7
        assert store.db.execute("SELECT state FROM runs").fetchone()[0] == "failed"
        with sqlite3.connect(store.backup_path) as backup:
            assert backup.execute("PRAGMA user_version").fetchone()[0] == version
    finally:
        store.close()


def test_identity_is_stable_and_foreign_open_is_refused(tmp_path):
    path = tmp_path / "state.sqlite3"
    identity = PlatformIdentity("owner", "machine", "fixture")
    store = SQLiteStore(path, identity=identity)
    environment = store.environment
    store.close()
    reopened = SQLiteStore(path, readonly=True, identity=identity)
    assert reopened.environment == environment
    reopened.close()
    for foreign in (PlatformIdentity("other", "machine", "fixture"),
                    PlatformIdentity("owner", "other", "fixture")):
        for readonly in (True, False):
            with pytest.raises(ConfigurationError, match="environment_mismatch"):
                SQLiteStore(path, readonly=readonly, identity=foreign)
    other = SQLiteStore(tmp_path / "other" / "state.sqlite3", identity=identity)
    assert other.environment != environment
    other.close()


def test_failed_upgrade_keeps_original_version_and_wal_backup(tmp_path):
    path = tmp_path / "state.sqlite3"
    legacy(path)
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("INSERT INTO trusted VALUES('wal-only','now')")
        db.execute("UPDATE sessions SET dirs='not json'")
        db.commit()
        with pytest.raises(ValueError):
            SQLiteStore(path)
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
        assert db.execute("SELECT root FROM sessions WHERE id='old'").fetchone()
        assert not db.execute(
            "SELECT name FROM sqlite_master WHERE name='local_environment'"
        ).fetchone()
    with sqlite3.connect(next(tmp_path.glob("*.bak"))) as backup:
        assert backup.execute("SELECT path FROM trusted WHERE path='wal-only'").fetchone()


def test_workspace_cas_event_rollback_and_active_run_guard(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    session = store.new_session(tmp_path)
    with SessionLock(tmp_path / "locks", session["id"]) as lock:
        args = dict(cwd=tmp_path / "new", granted_roots=[tmp_path], expected_version=0, lock=lock)
        store.db.execute("""CREATE TRIGGER fail_control BEFORE INSERT ON session_events
                            BEGIN SELECT RAISE(ABORT,'injected'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            store.update_workspace(session["id"], **args)
        assert store.session(session["id"]) == session
        store.db.execute("DROP TRIGGER fail_control")
        updated = store.update_workspace(session["id"], **args)
        assert updated["workspace_version"] == 1
        assert updated["updated"] == session["updated"]
        with pytest.raises(ContextConflictError):
            store.update_workspace(session["id"], **args)
        store.db.execute("INSERT INTO runs VALUES('r',?,'running','now','{}',NULL,0)",
                         (session["id"],))
        with pytest.raises(ValueError, match="active"):
            store.update_workspace(session["id"], **{**args, "expected_version": 1})
    with pytest.raises(ValueError, match="lock"):
        store.update_workspace(session["id"], **args)
    assert store.db.execute("SELECT count(*) FROM session_events").fetchone()[0] == 1
    store.close()
