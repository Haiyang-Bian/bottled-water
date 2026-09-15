"""SQLite memory ports. Every mutation is bounded, identity checked and transactional."""

import json
from dataclasses import asdict, replace
from uuid import uuid4

from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.memory import (
    MemoryAccessContext,
    MemoryCandidate,
    MemoryRecord,
    MemoryRevision,
    MemorySource,
)
from agent_runtime.core.run_types import utc_now
from agent_subsystems.memory.rules import (
    applicable,
    dumps,
    fingerprint,
    rank,
    selection,
    terms,
    validate,
)


def content_from(data):
    data = dict(data)
    data["tags"] = tuple(data.get("tags", ()))
    data["aliases"] = tuple(data.get("aliases", ()))
    return validate(MemoryRevision(**data))


class SQLiteMemory:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def access(self, *, scope_id=None, run_id=None):
        self.store._check_environment()
        env = self.store.environment
        if env is None:
            raise ConfigurationError("Memory is not enabled; run agenthub state upgrade")
        return MemoryAccessContext(env.environment_id, env.default_agent_id, scope_id, run_id)

    def check(self, access):
        expected = self.access()
        if (access.environment_id, access.agent_id) != (expected.environment_id, expected.agent_id):
            raise ConfigurationError("memory_identity_mismatch")
        if self.store.schema_version < 4:
            raise ConfigurationError("Memory is not enabled; run agenthub state upgrade")
        if access.scope_id and self.store.queries().session(access.scope_id) is None:
            raise ConfigurationError("memory_scope_mismatch")
        if access.run_id and self.store.queries().run_scope(access.run_id) != access.scope_id:
            raise ConfigurationError("memory_run_mismatch")

    def _row(self, access, memory_id):
        self.check(access)
        row = self.db.execute(
            "SELECT * FROM memories WHERE id=? AND environment=? AND agent=? AND status!='forgotten'",
            (memory_id, access.environment_id, access.agent_id),
        ).fetchone()
        if row is None:
            raise OperationError("memory_not_found", "No accessible memory")
        return row

    def _record(self, row, revision=None):
        revision = revision or row["revision"]
        saved = self.db.execute(
            "SELECT body FROM memory_revisions WHERE memory=? AND revision=?", (row["id"], revision)
        ).fetchone()
        if not saved:
            raise OperationError("revision_not_found", "No saved revision")
        sources = self.db.execute(
            "SELECT body FROM memory_sources WHERE memory=? AND revision=? ORDER BY source_key",
            (row["id"], revision),
        )
        return MemoryRecord(
            row["id"],
            revision,
            content_from(json.loads(saved[0])),
            tuple(MemorySource(**json.loads(s[0])) for s in sources),
            row["status"],
            bool(row["approved"]),
            row["verified"],
        )

    def read(self, access, memory_id, *, management=False, revision=None):
        row = self._row(access, memory_id)
        if not management and (row["status"] != "active" or not row["approved"]):
            raise OperationError("memory_not_found", "No accessible memory")
        return self._record(row, revision)

    def search(self, access, query="", *, cwd=None, offset=0, limit=20, management=False):
        self.check(access)
        if offset < 0 or not 1 <= limit <= 20 or len(query) > 2000:
            raise OperationError(
                "memory_query_limit", "Page limit: 1–20; query limit: 2000 characters"
            )
        sql = "SELECT m.* FROM memories m WHERE environment=? AND agent=? AND status!='forgotten'"
        args = [access.environment_id, access.agent_id]
        if not management:
            sql += " AND status='active' AND approved=1"
        wanted = sorted(terms(query))
        if wanted:
            sql += " AND EXISTS(SELECT 1 FROM memory_terms t WHERE t.memory=m.id AND term IN ("
            sql += ",".join("?" for _ in wanted) + "))"
            args.extend(wanted)
        records = [self._record(row) for row in self.db.execute(sql, args)]
        records.sort(key=lambda record: rank(record, query, cwd))
        return records[offset : offset + limit]

    def select(self, access, query, cwd, **budgets):
        self.check(access)
        # Query terms use the rebuildable index; pinned and applicable records are also considered.
        records = self.search(access, query, cwd=cwd, limit=20)
        ids = {r.id for r in records}
        rows = self.db.execute(
            "SELECT * FROM memories WHERE environment=? AND agent=? AND status='active' AND approved=1",
            (access.environment_id, access.agent_id),
        )
        for row in rows:
            record = self._record(row)
            if record.id not in ids and (
                record.content.basic
                or (record.content.directory and applicable(record.content.directory, cwd))
            ):
                records.append(record)
        return selection(records, query, cwd, **budgets)

    def revalidate(self, access, items):
        self.check(access)
        allowed = []
        for item in items:
            try:
                record = self.read(access, item.memory_id)
            except OperationError:
                continue
            if record.revision == item.revision:
                allowed.append(item)
        return allowed

    def _event(self, access, memory_id, operation, revision):
        self.db.execute(
            "INSERT INTO memory_events(environment,agent,memory,operation,revision,created) "
            "VALUES(?,?,?,?,?,?)",
            (
                access.environment_id,
                access.agent_id,
                memory_id,
                operation,
                revision,
                utc_now().isoformat(),
            ),
        )

    def _index(self, memory_id, content):
        self.db.execute("DELETE FROM memory_terms WHERE memory=?", (memory_id,))
        self.db.executemany(
            "INSERT INTO memory_terms VALUES(?,?)",
            [(memory_id, term) for term in sorted(terms(dumps(asdict(content))))],
        )

    def _revision(self, memory_id, revision, content, sources):
        self.db.execute(
            "INSERT INTO memory_revisions VALUES(?,?,?)",
            (memory_id, revision, dumps(asdict(content))),
        )
        for source in sources:
            data = asdict(source)
            self.db.execute("INSERT OR IGNORE INTO memory_lineage VALUES(?,?)",
                            (memory_id, fingerprint(data)))
            self.db.execute(
                "INSERT OR IGNORE INTO memory_sources VALUES(?,?,?,?)",
                (memory_id, revision, fingerprint(data), dumps(data)),
            )
        self._index(memory_id, content)

    def _save(self, access, content, sources):
        content = content_from(self.store.redactor.value(asdict(validate(content))))
        digest = fingerprint(asdict(content))
        duplicate = self.db.execute(
            "SELECT * FROM memories WHERE environment=? AND agent=? AND fingerprint=? "
            "AND status!='forgotten' ORDER BY id LIMIT 1",
            (access.environment_id, access.agent_id, digest),
        ).fetchone()
        if duplicate:
            # Explicit enable is required for disabled duplicates.
            self.db.executemany("INSERT OR IGNORE INTO memory_lineage VALUES(?,?)", [
                (duplicate["id"], fingerprint(asdict(source))) for source in sources
            ])
            return self._record(duplicate)
        memory_id = str(uuid4())
        self.db.execute(
            "INSERT INTO memories VALUES(?,?,?,1,'active',1,?,?)",
            (
                memory_id,
                access.environment_id,
                access.agent_id,
                digest,
                utc_now().isoformat(),
            ),
        )
        self._revision(memory_id, 1, content, sources)
        self._event(access, memory_id, "save", 1)
        return self.read(access, memory_id)

    def save(self, access, content):
        self.check(access)
        with self.store.transaction():
            return self._save(access, content, (MemorySource(),))

    def revise(self, access, memory_id, revision, content):
        validate(content)
        with self.store.transaction():
            row = self._row(access, memory_id)
            self._cas(row, revision)
            content = content_from(self.store.redactor.value(asdict(content)))
            old = self._record(row)
            if content.body != old.content.body:
                content = replace(content, evidence="user_stated")
            self._revision(memory_id, revision + 1, content, (*old.sources, MemorySource()))
            self.db.execute(
                "UPDATE memories SET revision=?,fingerprint=?,verified=? WHERE id=?",
                (revision + 1, fingerprint(asdict(content)), utc_now().isoformat(), memory_id),
            )
            self._event(access, memory_id, "revise", revision + 1)
            return self.read(access, memory_id, management=True)

    @staticmethod
    def _cas(row, revision):
        if row["revision"] != revision:
            raise OperationError(
                "memory_conflict", "Memory changed; read the latest revision first"
            )

    def set_status(self, access, memory_id, revision, status):
        if status not in {"active", "disabled", "forgotten"}:
            raise OperationError("invalid_status", "Invalid memory status")
        with self.store.transaction():
            row = self._row(access, memory_id)
            self._cas(row, revision)
            old = self._record(row)
            if status == "forgotten":
                keys = [
                    s[0]
                    for s in self.db.execute(
                    "SELECT source_key FROM memory_lineage WHERE memory=?", (memory_id,)
                    )
                ]
                # Remember lineage, not plaintext, including all historical revisions.
                keys += [
                    fingerprint({"memory_id": memory_id}),
                    fingerprint({"content": row["fingerprint"]}),
                ]
                self.db.executemany(
                    "INSERT OR IGNORE INTO memory_suppressed VALUES(?,?,?)",
                    [(access.environment_id, access.agent_id, key) for key in keys],
                )
                self.db.execute("DELETE FROM memory_terms WHERE memory=?", (memory_id,))
                self.db.execute("DELETE FROM memory_revisions WHERE memory=?", (memory_id,))
                self.db.execute("DELETE FROM memory_sources WHERE memory=?", (memory_id,))
            else:
                self._revision(memory_id, revision + 1, old.content, old.sources)
            self.db.execute(
                "UPDATE memories SET status=?,revision=? WHERE id=?",
                (status, revision + 1, memory_id),
            )
            self._event(access, memory_id, status, revision + 1)
        return {"id": memory_id, "revision": revision + 1, "status": status}

    def rebuild(self, access):
        self.check(access)
        with self.store.transaction():
            rows = self.db.execute(
                "SELECT * FROM memories WHERE environment=? AND agent=?",
                (access.environment_id, access.agent_id),
            ).fetchall()
            for row in rows:
                self.db.execute("DELETE FROM memory_terms WHERE memory=?", (row["id"],))
                if row["status"] != "forgotten":
                    self._index(row["id"], self._record(row).content)
        return {"indexed": sum(r["status"] != "forgotten" for r in rows)}

    def propose(self, access, call_id, content, sources):
        self.check(access)
        validate(content)
        if not access.run_id or not sources or len(sources) > 10:
            raise OperationError(
                "invalid_source", "Current Run and 1–10 source references required"
            )
        sources = [
            replace(s, run_id=s.run_id or access.run_id) if s.kind in {"request", "tool"} else s
            for s in sources
        ]
        with self.store.transaction():
            old = self.db.execute(
                "SELECT * FROM memory_candidates WHERE run=? AND call_id=?",
                (access.run_id, call_id),
            ).fetchone()
            if old:
                return self._candidate(old)
            run = self.db.execute("SELECT result FROM runs WHERE id=?", (access.run_id,)).fetchone()
            if run[0] is not None:
                raise OperationError("run_closed", "Cannot propose after Run completion")
            count = self.db.execute(
                "SELECT count(*) FROM memory_candidates WHERE run=?", (access.run_id,)
            ).fetchone()[0]
            if count >= 10:
                raise OperationError("candidate_limit", "At most 10 candidates per Run")
            candidate_id = str(uuid4())
            self.db.execute(
                "INSERT INTO memory_candidates VALUES(?,?,?,?,?,1,'pending',?,?,NULL,NULL)",
                (
                    candidate_id,
                    access.environment_id,
                    access.agent_id,
                    access.run_id,
                    call_id,
                    self.store.redactor.dumps(content),
                    self.store.redactor.dumps(sources),
                ),
            )
            return self.candidate(access, candidate_id)

    def _candidate(self, row):
        return MemoryCandidate(
            row["id"],
            row["revision"],
            row["status"],
            content_from(json.loads(row["body"])),
            tuple(MemorySource(**s) for s in json.loads(row["sources"])),
            row["reason"],
            row["memory"],
        )

    def candidate(self, access, candidate_id):
        self.check(access)
        row = self.db.execute(
            "SELECT * FROM memory_candidates WHERE id=? AND environment=? AND agent=?",
            (candidate_id, access.environment_id, access.agent_id),
        ).fetchone()
        if not row:
            raise OperationError("candidate_not_found", "No accessible candidate")
        return self._candidate(row)

    def candidates(self, access, *, offset=0, limit=20):
        self.check(access)
        if offset < 0 or not 1 <= limit <= 20:
            raise OperationError("memory_query_limit", "Page limit: 1–20")
        return [
            self._candidate(row)
            for row in self.db.execute(
                "SELECT * FROM memory_candidates WHERE environment=? AND agent=? "
                "AND status IN ('pending','ready','invalid') ORDER BY rowid LIMIT ? OFFSET ?",
                (access.environment_id, access.agent_id, limit, offset),
            )
        ]

    def candidate_count(self, access, run_id=None):
        self.check(access)
        sql = (
            "SELECT count(*) FROM memory_candidates WHERE environment=? AND agent=? "
            "AND status IN ('pending','ready','invalid')"
        )
        args = [access.environment_id, access.agent_id]
        if run_id:
            sql += " AND run=?"
            args.append(run_id)
        return self.db.execute(sql, args).fetchone()[0]

    def _validate_sources(self, access, candidate, scope):
        verified = []
        for source in candidate.sources:
            if source.kind == "request":
                row = self.db.execute(
                    "SELECT scope,request FROM runs WHERE id=?", (source.run_id,)
                ).fetchone()
                if not row or row["scope"] != scope:
                    raise OperationError("invalid_source", "Request must belong to this task")
                text = json.loads(row["request"]).get("input", "")
                clean = MemorySource(kind="request", run_id=source.run_id)
            elif source.kind == "tool":
                rows = self.db.execute(
                    "SELECT e.body FROM events e JOIN runs r ON r.id=e.run "
                    "WHERE e.run=? AND r.scope=? ORDER BY e.sequence",
                    (source.run_id, scope),
                )
                event = next(
                    (
                        e
                        for r in rows
                        if (e := json.loads(r[0])).get("type") == "agent.tool_result"
                        and e.get("payload", {}).get("call_id") == source.call_id
                        and (source.sequence is None or e.get("sequence") == source.sequence)
                    ),
                    {},
                )
                payload = event.get("payload", {})
                if (
                    event.get("type") != "agent.tool_result"
                    or payload.get("call_id") != source.call_id
                    or not payload.get("success")
                    or str(payload.get("tool", "")).startswith("memory.")
                ):
                    raise OperationError("invalid_source", "No confirmed tool observation")
                result = payload.get("result") or {}
                text = dumps(result)
                if not isinstance(result, dict):
                    result = {}
                clean = MemorySource(
                    kind="tool",
                    run_id=source.run_id,
                    sequence=event.get("sequence"),
                    call_id=source.call_id,
                    path=result.get("path"),
                    sha256=result.get("sha256"),
                    incomplete=bool(result.get("truncated")),
                )
            elif source.kind == "memory":
                record = self.read(access, source.memory_id)
                if record.revision != source.revision:
                    raise OperationError(
                        "invalid_source", "Referenced memory revision is unavailable"
                    )
                text = record.content.body
                clean = MemorySource(kind="memory", memory_id=record.id, revision=record.revision)
            else:
                raise OperationError("invalid_source", "Unsupported candidate source")
            if candidate.content.evidence == "observed" and (
                source.kind != "tool" or candidate.content.body not in text
            ):
                raise OperationError(
                    "unverified_claim",
                    "Observed content must quote saved tool facts; use inferred for conclusions",
                )
            if candidate.content.evidence == "user_stated" and (
                source.kind != "request" or candidate.content.body not in text
            ):
                raise OperationError(
                    "unverified_claim", "User-stated content must quote a saved user request"
                )
            if (
                candidate.content.kind == "preference"
                and candidate.content.evidence != "user_stated"
            ):
                raise OperationError(
                    "unverified_preference", "External observations cannot become user preferences"
                )
            verified.append(clean)
        return tuple(verified)

    def _suppressed(self, access, content, sources):
        keys = [fingerprint(asdict(s)) for s in sources]
        keys += [fingerprint({"memory_id": s.memory_id}) for s in sources if s.memory_id]
        keys.append(fingerprint({"content": fingerprint(asdict(content))}))
        return any(
            self.db.execute(
                "SELECT 1 FROM memory_suppressed WHERE environment=? AND agent=? AND source_key=?",
                (access.environment_id, access.agent_id, key),
            ).fetchone()
            for key in keys
        )

    def process(self, access, limit=20):
        self.check(access)
        if not 1 <= limit <= 20:
            raise OperationError("memory_query_limit", "Process limit: 1–20 Runs")
        jobs = self.db.execute(
            "SELECT j.run,r.scope FROM memory_jobs j JOIN runs r ON r.id=j.run "
            "JOIN sessions s ON s.id=r.scope WHERE j.state!='done' AND r.result IS NOT NULL "
            "AND s.environment_id=? ORDER BY r.created,r.id LIMIT ?",
            (access.environment_id, limit),
        ).fetchall()
        done = 0
        for job in jobs:
            with self.store.transaction():
                rows = self.db.execute(
                    "SELECT * FROM memory_candidates WHERE run=? AND status='pending' "
                    "AND environment=? AND agent=?",
                    (job["run"], access.environment_id, access.agent_id),
                ).fetchall()
                for row in rows:
                    candidate = self._candidate(row)
                    status, reason, sources = "ready", None, candidate.sources
                    try:
                        sources = self._validate_sources(access, candidate, job["scope"])
                        if self._suppressed(access, candidate.content, sources):
                            raise OperationError(
                                "suppressed",
                                "Previously forgotten source; explicit user save required",
                            )
                    except OperationError as exc:
                        status, reason = "invalid", exc.code
                    self.db.execute(
                        "UPDATE memory_candidates SET status=?,reason=?,sources=? WHERE id=?",
                        (status, reason, dumps([asdict(s) for s in sources]), candidate.id),
                    )
                self.db.execute(
                    "UPDATE memory_jobs SET state='done',cursor=?,error=NULL WHERE run=?",
                    (len(rows), job["run"]),
                )
            done += 1
        return {"processed_runs": done, "candidates": self.candidate_count(access)}

    def decide(self, access, candidate_id, revision, *, adopt=False, content=None):
        self.check(access)
        with self.store.transaction():
            candidate = self.candidate(access, candidate_id)
            if candidate.status == "adopted" and adopt:
                return {"memory_id": candidate.memory_id, "status": "adopted"}
            if candidate.revision != revision:
                raise OperationError("memory_conflict", "Candidate changed; read again")
            if not adopt:
                if candidate.status == "adopted":
                    raise OperationError("candidate_already_adopted", "Use memory disable or forget for adopted knowledge")
                self.db.execute(
                    "UPDATE memory_candidates SET status='rejected',revision=revision+1 WHERE id=?",
                    (candidate_id,),
                )
                self._event(access, candidate_id, "candidate_rejected", revision + 1)
                return {"id": candidate_id, "status": "rejected"}
            if candidate.status != "ready":
                raise OperationError(
                    "candidate_not_ready", "Only validated candidates can be adopted"
                )
            scope = self.db.execute(
                "SELECT r.scope FROM runs r JOIN memory_candidates c ON c.run=r.id WHERE c.id=?",
                (candidate_id,),
            ).fetchone()[0]
            candidate = replace(candidate, content=content or candidate.content)
            sources = self._validate_sources(access, candidate, scope)
            if self._suppressed(access, candidate.content, sources):
                raise OperationError("suppressed", "Forgotten source cannot be adopted again")
            record = self._save(access, candidate.content, sources)
            self.db.execute(
                "UPDATE memory_candidates SET status='adopted',memory=?,revision=revision+1,body=?,sources=? WHERE id=?",
                (record.id, dumps(asdict(candidate.content)), dumps([asdict(s) for s in sources]), candidate_id),
            )
            self._event(access, record.id, "candidate_adopted", record.revision)
            return {
                "memory_id": record.id,
                "revision": record.revision,
                "status": "adopted",
                "memory_status": record.status,
            }

    def used(self, access, run_id=None):
        self.check(access)
        if run_id and self.store.queries().run_scope(run_id) is None:
            raise OperationError("run_not_found", "No accessible Run")
        sql = "SELECT e.body FROM events e JOIN runs r ON e.run=r.id JOIN sessions s ON s.id=r.scope WHERE s.environment_id=?"
        args = [access.environment_id]
        if run_id:
            sql += " AND r.id=?"
            args.append(run_id)
        elif access.scope_id:
            sql += " AND r.id=(SELECT id FROM runs WHERE scope=? ORDER BY created DESC,id DESC LIMIT 1)"
            args.append(access.scope_id)
        return [
            data
            for row in self.db.execute(sql + " ORDER BY r.created,e.sequence", args)
            if (data := json.loads(row[0])).get("type") == "agent.memory_used"
        ]
