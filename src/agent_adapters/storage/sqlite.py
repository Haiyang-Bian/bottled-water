"""Local SQLite authority for contexts, ordered events, sessions and trust.

Transactions contain bounded local SQL only; no model or tool awaits occur inside them.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import (
    AgentMemory,
    ContextSnapshot,
    EventEnvelope,
    EventPage,
    RunResult,
    RunState,
    Usage,
    utc_now,
)
from agent_runtime.runtime.run_journal import (
    EventSequenceConflictError,
    sanitize_event_for_persistence,
)
from agent_subsystems.observability.redaction import Redactor


class SQLiteStore:
    def __init__(self, path, redactor=None):
        self.redactor = redactor or Redactor()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=1, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            self.db.close()
            raise ValueError(f"Unsupported state database version: {version}")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS trusted(path TEXT PRIMARY KEY, created TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sessions(
          id TEXT PRIMARY KEY, root TEXT NOT NULL, dirs TEXT NOT NULL,
          created TEXT NOT NULL, updated TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS sessions_root ON sessions(root, updated);
        CREATE TABLE IF NOT EXISTS contexts(scope TEXT PRIMARY KEY, version INTEGER NOT NULL,
          body TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, scope TEXT NOT NULL,
          state TEXT NOT NULL, created TEXT NOT NULL, request TEXT NOT NULL,
          result TEXT, sequence INTEGER NOT NULL DEFAULT 0);
        CREATE INDEX IF NOT EXISTS runs_scope ON runs(scope);
        CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY, run TEXT NOT NULL REFERENCES runs(id),
          sequence INTEGER NOT NULL, body TEXT NOT NULL, UNIQUE(run, sequence));
        PRAGMA user_version=1;
        """)

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def close(self):
        self.db.close()

    def is_trusted(self, path):
        return (
            self.db.execute("SELECT 1 FROM trusted WHERE path=?", (str(path),)).fetchone()
            is not None
        )

    def trust(self, path, enabled=True):
        if enabled:
            self.db.execute(
                "INSERT OR IGNORE INTO trusted VALUES(?,?)", (str(path), utc_now().isoformat())
            )
        else:
            self.db.execute("DELETE FROM trusted WHERE path=?", (str(path),))

    def new_session(self, root):
        session = {
            "id": str(uuid4()),
            "root": str(root),
            "dirs": [],
            "created": utc_now().isoformat(),
            "updated": utc_now().isoformat(),
        }
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?)",
            (
                session["id"],
                session["root"],
                "[]",
                session["created"],
                session["updated"],
            ),
        )
        return session

    def sessions(self, root=None):
        query = "SELECT * FROM sessions"
        args = ()
        if root is not None:
            query += " WHERE root=?"
            args = (str(root),)
        rows = self.db.execute(query + " ORDER BY updated DESC", args).fetchall()
        return [{**dict(row), "dirs": json.loads(row["dirs"])} for row in rows]

    def session(self, identifier):
        return next((s for s in self.sessions() if s["id"] == identifier), None)

    def set_directories(self, session_id, directories):
        self.db.execute(
            "UPDATE sessions SET dirs=?,updated=? WHERE id=?",
            (
                json.dumps([str(p) for p in directories]),
                utc_now().isoformat(),
                session_id,
            ),
        )

    async def load(self, scope_id):
        row = self.db.execute("SELECT body FROM contexts WHERE scope=?", (scope_id,)).fetchone()
        if row is None:
            return ContextSnapshot(scope_id)
        data = json.loads(row["body"])
        return ContextSnapshot(
            scope_id=scope_id,
            version=data["version"],
            messages=tuple(data["messages"]),
            blackboard=data["blackboard"],
            agent_memories={
                k: AgentMemory(
                    **{
                        **v,
                        **{
                            name: tuple(v.get(name, ()))
                            for name in ("completed_tasks", "blockers", "facts", "output_refs")
                        },
                    }
                )
                for k, v in data["agent_memories"].items()
            },
        )

    async def commit(self, scope_id, delta):
        with self.transaction():
            current = await self.load(scope_id)
            if current.version != delta.expected_version:
                raise ContextConflictError("Context changed since this Run started")
            updated = ContextSnapshot(
                scope_id,
                current.version + 1,
                tuple(delta.messages),
                delta.blackboard,
                {**current.agent_memories, **delta.agent_memories},
            )
            self.db.execute(
                "INSERT OR REPLACE INTO contexts VALUES(?,?,?)",
                (
                    scope_id,
                    updated.version,
                    self.redactor.dumps(updated),
                ),
            )
        return await self.load(scope_id)

    async def create_run(self, request, snapshot):
        self.db.execute(
            "INSERT INTO runs(id,scope,state,created,request) VALUES(?,?,?,?,?)",
            (
                request.run_id,
                request.context_scope_id,
                snapshot.state.value,
                (snapshot.started_at or utc_now()).isoformat(),
                self.redactor.dumps({"input": request.input, "metadata": request.metadata}),
            ),
        )
        self.db.execute(
            "UPDATE sessions SET updated=? WHERE id=?",
            (
                utc_now().isoformat(),
                request.context_scope_id,
            ),
        )

    def _append(self, event):
        event = sanitize_event_for_persistence(event)
        body = self.redactor.dumps(event)
        old = self.db.execute("SELECT body FROM events WHERE id=?", (event.event_id,)).fetchone()
        if old is not None:
            if old[0] == body:
                return
            raise EventSequenceConflictError("Event id already used")
        run = self.db.execute("SELECT sequence FROM runs WHERE id=?", (event.run_id,)).fetchone()
        if run is None:
            raise KeyError(event.run_id)
        if run[0] + 1 != event.sequence:
            raise EventSequenceConflictError("Non-contiguous event sequence")
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?)",
            (
                event.event_id,
                event.run_id,
                event.sequence,
                body,
            ),
        )
        self.db.execute("UPDATE runs SET sequence=? WHERE id=?", (event.sequence, event.run_id))

    async def append_event(self, event):
        with self.transaction():
            self._append(event)

    async def try_finish(self, result, terminal_event):
        with self.transaction():
            row = self.db.execute("SELECT result FROM runs WHERE id=?", (result.run_id,)).fetchone()
            if row is None:
                raise KeyError(result.run_id)
            if row[0] is not None:
                return False
            self._append(terminal_event)
            self.db.execute(
                "UPDATE runs SET state=?,result=? WHERE id=?",
                (
                    result.state.value,
                    self.redactor.dumps(result),
                    result.run_id,
                ),
            )
        return True

    async def read_events(self, run_id, *, after_sequence=0, limit=200):
        if after_sequence < 0 or not 0 < limit <= 10000:
            raise ValueError("Invalid event cursor or limit")
        row = self.db.execute("SELECT sequence,result FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        rows = self.db.execute(
            "SELECT body FROM events WHERE run=? AND sequence>? ORDER BY sequence LIMIT ?",
            (run_id, after_sequence, limit),
        ).fetchall()
        items = []
        for item in rows:
            data = json.loads(item[0])
            data["occurred_at"] = datetime.fromisoformat(data["occurred_at"])
            items.append(EventEnvelope(**data))
        return EventPage(
            tuple(items),
            items[-1].sequence if items else after_sequence,
            row[0],
            row[1] is not None,
        )

    async def recover_session(self, scope_id):
        """Caller must hold the session's OS lock before recovering abandoned runs."""
        rows = self.db.execute(
            "SELECT * FROM runs WHERE scope=? AND result IS NULL", (scope_id,)
        ).fetchall()
        for row in rows:
            result = RunResult(
                row["id"],
                scope_id,
                RunState.FAILED,
                "process_lost",
                datetime.fromisoformat(row["created"]),
                utc_now(),
                Usage(),
                (await self.load(scope_id)).version,
            )
            event = EventEnvelope(
                row["id"],
                scope_id,
                row["sequence"] + 1,
                "system.run_failed",
                {"reason_code": "process_lost"},
            )
            await self.try_finish(result, event)

    def run_result(self, run_id):
        row = self.db.execute("SELECT result FROM runs WHERE id=?", (run_id,)).fetchone()
        return json.loads(row[0]) if row is not None and row[0] else None
