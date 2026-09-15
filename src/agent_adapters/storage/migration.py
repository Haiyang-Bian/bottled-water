"""Exclusive, backed-up SQLite schema migrations; one transaction per upgrade."""

import json
import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone
from uuid import uuid4

from agent_contracts.errors import ConfigurationError
from agent_contracts.identity import LocalEnvironment
from .session_lock import SessionLock
from .memory_schema import MEMORY_SCHEMA
from .resource_schema import RESOURCE_SCHEMA

SCHEMA_VERSION = 5

BASE_SCHEMA = (
    "CREATE TABLE trusted(path TEXT PRIMARY KEY, created TEXT NOT NULL)",
    "CREATE TABLE contexts(scope TEXT PRIMARY KEY, version INTEGER NOT NULL, body TEXT NOT NULL)",
    "CREATE TABLE runs(id TEXT PRIMARY KEY, scope TEXT NOT NULL, state TEXT NOT NULL, "
    "created TEXT NOT NULL, request TEXT NOT NULL, result TEXT, sequence INTEGER NOT NULL DEFAULT 0)",
    "CREATE INDEX runs_scope ON runs(scope)",
    "CREATE TABLE events(id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id), "
    "sequence INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(run,sequence))",
)

LOCAL_SCHEMA = (
    "CREATE TABLE local_environment(singleton INTEGER PRIMARY KEY CHECK(singleton=1), "
    "environment_id TEXT UNIQUE NOT NULL, owner_key TEXT NOT NULL, machine_key TEXT NOT NULL, "
    "binding_kind TEXT NOT NULL, default_agent_id TEXT NOT NULL)",
    "CREATE TABLE sessions_v3(id TEXT PRIMARY KEY, environment_id TEXT NOT NULL "
    "REFERENCES local_environment(environment_id), origin_root TEXT NOT NULL, cwd TEXT NOT NULL, "
    "granted_roots TEXT NOT NULL, workspace_version INTEGER NOT NULL DEFAULT 0, "
    "created TEXT NOT NULL, updated TEXT NOT NULL)",
)


def read_environment(db, identity):
    row = db.execute("SELECT * FROM local_environment WHERE singleton=1").fetchone()
    if row is None or (row["owner_key"], row["machine_key"], row["binding_kind"]) != (
        identity.owner_key, identity.machine_key, identity.binding_kind
    ):
        raise ConfigurationError(
            "environment_mismatch: this state belongs to another owner or machine; "
            "use its original environment or a separate AGENTHUB_HOME"
        )
    return LocalEnvironment(row["environment_id"], row["default_agent_id"])


def migrate(db, path, version, identity):
    # Caller holds the migration gate. Older clients use the same gate and session locks.
    with ExitStack() as locks:
        if version:
            for row in db.execute("SELECT id FROM sessions ORDER BY id"):
                locks.enter_context(SessionLock(path.parent / "locks", row[0], guard=False))
        backup_path = None
        if version:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
            backup_path = path.with_name(path.name + f".v{version}-{stamp}.bak")
            with sqlite3.connect(backup_path) as backup:
                db.backup(backup)
        db.execute("BEGIN IMMEDIATE")
        try:
            if version == 0:
                for statement in BASE_SCHEMA:
                    db.execute(statement)
            if version < 2:
                db.execute("CREATE TABLE continuation_metadata(scope TEXT PRIMARY KEY, body TEXT)")
            if version < 3:
                migrate_local_environment(db, version, identity)
            if version < 4:
                for statement in MEMORY_SCHEMA:
                    db.execute(statement)
            if version < 5:
                for statement in RESOURCE_SCHEMA:
                    db.execute(statement)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return backup_path


def migrate_local_environment(db, version, identity):
    for statement in LOCAL_SCHEMA:
        db.execute(statement)
    environment_id = str(uuid4())
    db.execute("INSERT INTO local_environment VALUES(1,?,?,?,?,?)", (
        environment_id, identity.owner_key, identity.machine_key, identity.binding_kind, "local",
    ))
    if version:
        for row in db.execute("SELECT * FROM sessions").fetchall():
            dirs = json.loads(row["dirs"])
            if not isinstance(dirs, list) or not all(isinstance(p, str) for p in dirs):
                raise ValueError("Invalid legacy directory record")
            roots = list(dict.fromkeys([row["root"], *dirs]))
            db.execute("INSERT INTO sessions_v3 VALUES(?,?,?,?,?,?,?,?)", (
                row["id"], environment_id, row["root"], row["root"],
                json.dumps(roots), 0, row["created"], row["updated"],
            ))
        db.execute("DROP TABLE sessions")
    db.execute("ALTER TABLE sessions_v3 RENAME TO sessions")
    db.execute("CREATE INDEX sessions_location ON sessions(environment_id,cwd)")
    db.execute(
        "CREATE TABLE session_events(session_id TEXT NOT NULL REFERENCES sessions(id), "
        "version INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(session_id,version))"
    )
