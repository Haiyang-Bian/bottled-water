"""Local SQLite authority for contexts, ordered events, sessions and trust.

Transactions contain bounded local SQL only; no model or tool awaits occur inside them.
"""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from uuid import uuid4

from agent_runtime.core.ports import ContextConflictError
from agent_contracts.persistence import ContinuationRun
from .session_lock import SessionLock
from .migration import SCHEMA_VERSION, migrate, read_environment
from .session_queries import SessionQueries
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
    def __init__(self, path, redactor=None, *, readonly=False, identity=None):
        self.redactor = redactor or Redactor()
        self.path = path
        self.backup_path = None
        self.environment = None
        self.identity = identity
        if readonly:
            self.db = sqlite3.connect(
                path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None
            )
            self.db.row_factory = sqlite3.Row
            self.schema_version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if self.schema_version not in (1, 2, SCHEMA_VERSION):
                self.db.close()
                raise ValueError("Unsupported state database version")
            try:
                self._check_environment()
            except BaseException:
                self.db.close()
                raise
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with SessionLock(path.parent / "locks", "__migration__", guard=False):
            self.db = sqlite3.connect(path, timeout=1, isolation_level=None)
            try:
                self._initialize(path)
            except BaseException:
                self.db.close()
                raise

    def _initialize(self, path):
        self.db.row_factory = sqlite3.Row
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1, 2, SCHEMA_VERSION):
            raise ValueError(f"Unsupported state database version: {version}")
        self.schema_version = version
        self._check_environment()  # Refuse a foreign binding before any persistent writes.
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        if version < SCHEMA_VERSION:
            self.backup_path = migrate(self.db, path, version, self._identity())
            self.schema_version = SCHEMA_VERSION
            self._check_environment()

    def _identity(self):
        if self.identity is None:
            from agent_adapters.local.identity import current_identity
            self.identity = current_identity()
        return self.identity

    def _check_environment(self):
        if self.schema_version >= 3:
            self.environment = read_environment(self.db, self._identity())

    def queries(self):
        self._check_environment()
        return SessionQueries(self.db, self.environment)

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

    def new_session(self, root, *, cwd=None, granted_roots=None):
        session = {
            "id": str(uuid4()),
            "environment_id": self.environment.environment_id,
            "origin_root": str(root),
            "cwd": str(cwd or root),
            "granted_roots": list(dict.fromkeys(map(str, granted_roots or [root]))),
            "workspace_version": 0,
            "created": utc_now().isoformat(),
            "updated": utc_now().isoformat(),
        }
        self.db.execute(
            "INSERT INTO sessions VALUES(?,?,?,?,?,?,?,?)",
            (
                session["id"],
                session["environment_id"], session["origin_root"], session["cwd"],
                json.dumps(session["granted_roots"]), 0,
                session["created"],
                session["updated"],
            ),
        )
        return session

    def sessions(self, root=None):
        query = "SELECT id FROM sessions WHERE environment_id=?"
        args = [self.environment.environment_id]
        if root is not None:
            query += " AND cwd=?"
            args.append(str(root))
        rows = self.db.execute(query + " ORDER BY updated DESC", args).fetchall()
        return [self.session(row["id"]) for row in rows]

    def session(self, identifier):
        return self.queries().session(identifier)

    def update_workspace(self, session_id, *, cwd, granted_roots, expected_version, lock):
        if not lock.owns(self.path.parent / "locks", session_id):
            raise ValueError("Session lock is required for a workspace update")
        with self.transaction():
            current = self.session(session_id)
            if current is None or current["workspace_version"] != expected_version:
                raise ContextConflictError("Session workspace changed; reload it before retrying")
            if self.db.execute(
                "SELECT 1 FROM runs WHERE scope=? AND result IS NULL", (session_id,)
            ).fetchone():
                raise ValueError("Cannot change workspace while a Run is active")
            roots = list(dict.fromkeys(map(str, granted_roots)))
            if current["cwd"] == str(cwd) and current["granted_roots"] == roots:
                return current
            version = expected_version + 1
            self.db.execute(
                "UPDATE sessions SET cwd=?,granted_roots=?,workspace_version=? "
                "WHERE id=? AND workspace_version=?", (str(cwd), json.dumps(roots), version,
                                                       session_id, expected_version),
            )
            self.db.execute("INSERT INTO session_events VALUES(?,?,?)", (
                session_id, version, self.redactor.dumps({
                    "type": "session.workspace_changed", "actor": "user",
                    "created": utc_now().isoformat(), "previous_cwd": current["cwd"],
                    "cwd": str(cwd), "granted_roots": roots, "version": version,
                }),
            ))
        return self.session(session_id)

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
            continuation=self._continuation(scope_id),
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

    def _continuation(self, scope_id):
        if self.schema_version < 2:
            return {}
        row = self.db.execute(
            "SELECT body FROM continuation_metadata WHERE scope=?", (scope_id,)
        ).fetchone()
        return json.loads(row[0]) if row else {}

    async def _commit_in_transaction(self, scope_id, delta):
        current = await self.load(scope_id)
        if current.version != delta.expected_version:
            raise ContextConflictError("Context changed since this Run started")
        updated = ContextSnapshot(
            scope_id,
            current.version + 1,
            tuple(delta.messages),
            delta.blackboard,
            {**current.agent_memories, **delta.agent_memories},
            delta.continuation,
        )
        self.db.execute(
            "INSERT OR REPLACE INTO contexts VALUES(?,?,?)",
            (scope_id, updated.version, self.redactor.dumps(updated)),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO continuation_metadata VALUES(?,?)",
            (scope_id, self.redactor.dumps(delta.continuation)),
        )
        return updated

    async def commit(self, scope_id, delta):
        with self.transaction():
            await self._commit_in_transaction(scope_id, delta)
        return await self.load(scope_id)

    async def try_complete(self, delta, result, terminal_event):
        with self.transaction():
            row = self.db.execute(
                "SELECT result,scope FROM runs WHERE id=?", (result.run_id,)
            ).fetchone()
            if row is None or row["scope"] != result.context_scope_id:
                raise KeyError(result.run_id)
            if row["result"] is not None:
                return None
            await self._commit_in_transaction(result.context_scope_id, delta)
            self._append(terminal_event)
            self.db.execute(
                "UPDATE runs SET state=?,result=? WHERE id=?",
                (result.state.value, self.redactor.dumps(result), result.run_id),
            )
        return await self.load(result.context_scope_id)

    async def list_scope_runs(self, scope_id):
        rows = self.db.execute(
            "SELECT * FROM runs WHERE scope=? ORDER BY created,id", (scope_id,)
        ).fetchall()
        return [
            ContinuationRun(
                row["id"],
                scope_id,
                json.loads(row["request"]).get("input", ""),
                row["state"],
                (json.loads(row["result"]) if row["result"] else {}).get("reason_code"),
                row["sequence"],
            )
            for row in rows
        ]

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
            usage = Usage(incomplete=True)
            saved = self.db.execute(
                "SELECT body FROM events WHERE run=? ORDER BY sequence DESC", (row["id"],)
            ).fetchall()
            for item in saved:
                data = json.loads(item[0])
                if data["type"] == "execution.usage":
                    fields = data["payload"].get("run_usage", {})
                    usage = Usage(
                        **{k: v for k, v in fields.items() if k in Usage.__dataclass_fields__}
                    )
                    usage.incomplete = True
                    break
            result = RunResult(
                row["id"],
                scope_id,
                RunState.FAILED,
                "process_lost",
                datetime.fromisoformat(row["created"]),
                utc_now(),
                usage,
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
