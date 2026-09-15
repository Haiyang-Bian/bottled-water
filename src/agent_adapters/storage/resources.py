"""Transactional resource catalog. Reads expose saved facts, never probe a filesystem."""

import json
import os
from dataclasses import asdict, replace
from pathlib import Path
from uuid import uuid4

from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.resources import (
    ResourceAccessContext,
    ResourceRecord,
    ResourceRevision,
    ResourceObservation,
    ResourceSource,
    SoftwareSpec,
)
from agent_runtime.core.run_types import utc_now
from agent_subsystems.memory.rules import dumps, fingerprint, terms
from agent_subsystems.workspaces.resources import observations, ranking, validate, FACTS


def content(value):
    value = dict(value)
    value["aliases"] = tuple(value.get("aliases", ()))
    return ResourceRevision(**value)


class SQLiteResources:
    def __init__(self, store):
        self.store, self.db = store, store.db

    def access(self, *, scope_id=None, run_id=None):
        self.store._check_environment()
        env = self.store.environment
        if env is None:
            raise ConfigurationError("Resources unavailable; run agenthub state upgrade")
        return ResourceAccessContext(env.environment_id, env.default_agent_id, scope_id, run_id)

    def check(self, access):
        expected = self.access()
        if (access.environment_id, access.agent_id) != (expected.environment_id, expected.agent_id):
            raise ConfigurationError("resource_identity_mismatch")
        if self.store.schema_version < 5:
            raise ConfigurationError("Resources unavailable; run agenthub state upgrade")
        queries = self.store.queries()
        if access.scope_id and queries.session(access.scope_id) is None:
            raise ConfigurationError("resource_scope_mismatch")
        if access.run_id and queries.run_scope(access.run_id) != access.scope_id:
            raise ConfigurationError("resource_run_mismatch")

    def _row(self, access, identifier):
        self.check(access)
        row = self.db.execute(
            "SELECT * FROM resources WHERE id=? AND environment=? AND agent=?",
            (identifier, access.environment_id, access.agent_id),
        ).fetchone()
        if row is None:
            raise OperationError("resource_not_found", "No accessible resource")
        return row

    def _record(self, row, revision=None):
        revision = revision or row["revision"]
        saved = self.db.execute(
            "SELECT body FROM resource_revisions WHERE resource=? AND revision=?",
            (row["id"], revision),
        ).fetchone()
        if not saved:
            raise OperationError("revision_not_found", "No saved resource revision")
        c = content(json.loads(saved[0]))
        obs = self.db.execute(
            "SELECT body FROM resource_observations WHERE resource=? "
            "AND json_extract(body,'$.path')=? "
            "ORDER BY json_extract(body,'$.observed_at') DESC,id DESC LIMIT 1",
            (row["id"], c.path),
        ).fetchone()
        observation = None
        if obs:
            data = json.loads(obs[0])
            data["source"] = ResourceSource(**data["source"])
            observation = ResourceObservation(**data)
        return ResourceRecord(row["id"], revision, c, row["status"], observation)

    def read(self, access, identifier, *, management=False, revision=None):
        row = self._row(access, identifier)
        if not management and row["status"] != "active":
            raise OperationError("resource_not_found", "No accessible resource")
        return self._record(row, revision)

    def search(self, access, query="", *, offset=0, limit=20, management=False, kind=None):
        self.check(access)
        if offset < 0 or not 1 <= limit <= 20 or len(query) > 2000:
            raise OperationError(
                "resource_query_limit", "Page: 1–20; query: at most 2000 characters"
            )
        sql = "SELECT r.* FROM resources r WHERE environment=? AND agent=?"
        args = [access.environment_id, access.agent_id]
        if not management:
            sql += " AND status='active'"
        wanted = sorted(terms(query))
        if wanted:
            sql += " AND EXISTS(SELECT 1 FROM resource_terms t WHERE t.resource=r.id AND term IN ("
            sql += ",".join("?" for _ in wanted) + "))"
            args.extend(wanted)
        records = [self._record(r) for r in self.db.execute(sql, args)]
        if kind:
            records = [r for r in records if r.content.kind == kind]
        records.sort(key=lambda r: ranking(r, query))
        return records[offset : offset + limit]

    def _index(self, identifier, value):
        self.db.execute("DELETE FROM resource_terms WHERE resource=?", (identifier,))
        self.db.executemany(
            "INSERT INTO resource_terms VALUES(?,?)",
            [(identifier, t) for t in sorted(terms(dumps(asdict(value))))],
        )

    def _event(self, access, identifier, operation, revision, body=None):
        self.db.execute(
            "INSERT INTO resource_events(environment,agent,resource,operation,revision,body,created) "
            "VALUES(?,?,?,?,?,?,?)",
            (
                access.environment_id,
                access.agent_id,
                identifier,
                operation,
                revision,
                self.store.redactor.dumps(body or {}),
                utc_now().isoformat(),
            ),
        )

    def _save(self, access, value):
        existing = self.db.execute(
            "SELECT * FROM resources WHERE environment=? AND agent=? AND path=?",
            (access.environment_id, access.agent_id, value.path),
        ).fetchone()
        if existing:
            return self._record(existing)
        identifier = str(uuid4())
        self.db.execute(
            "INSERT INTO resources VALUES(?,?,?,1,'active',?)",
            (identifier, access.environment_id, access.agent_id, value.path),
        )
        self.db.execute(
            "INSERT INTO resource_revisions VALUES(?,1,?)", (identifier, dumps(asdict(value)))
        )
        self._index(identifier, value)
        self._event(access, identifier, "registered", 1)
        return self.read(access, identifier)

    def save(self, access, value):
        self.check(access)
        value = validate(content(self.store.redactor.value(asdict(validate(value)))))
        with self.store.transaction():
            return self._save(access, value)

    def _revise(self, access, identifier, revision, value, status=None):
        row = self._row(access, identifier)
        if row["revision"] != revision:
            raise OperationError("resource_conflict", "Resource changed; reread its revision")
        duplicate = self.db.execute(
            "SELECT id FROM resources WHERE environment=? AND agent=? AND path=? AND id!=?",
            (access.environment_id, access.agent_id, value.path, identifier),
        ).fetchone()
        if duplicate:
            raise OperationError("resource_location_conflict", "Location already has a resource")
        self.db.execute(
            "UPDATE resources SET revision=?,path=?,status=? WHERE id=?",
            (revision + 1, value.path, status or row["status"], identifier),
        )
        self.db.execute(
            "INSERT INTO resource_revisions VALUES(?,?,?)",
            (identifier, revision + 1, dumps(asdict(value))),
        )
        self._index(identifier, value)
        self._event(access, identifier, "revised" if status is None else status, revision + 1)
        return self.read(access, identifier, management=True)

    def revise(self, access, identifier, revision, value):
        self.check(access)
        value = validate(content(self.store.redactor.value(asdict(validate(value)))))
        with self.store.transaction():
            return self._revise(access, identifier, revision, value)

    def set_status(self, access, identifier, revision, status):
        if status not in {"active", "disabled"}:
            raise OperationError("invalid_resource_status", "Use active or disabled")
        with self.store.transaction():
            record = self.read(access, identifier, management=True)
            result = self._revise(access, identifier, revision, record.content, status)
            if status == "disabled":
                cfg = self.db.execute(
                    "SELECT body FROM software WHERE resource=?", (identifier,)
                ).fetchone()
                if cfg:
                    body = {**json.loads(cfg[0]), "enabled": False}
                    self.db.execute(
                        "UPDATE software SET body=? WHERE resource=?", (dumps(body), identifier)
                    )
            return result

    def _observe(self, access, path, facts, source, relation="observed", observed_at=None):
        # A persisted observation's actual path must not be re-resolved against today's links.
        if not Path(path).is_absolute():
            raise OperationError("invalid_observation", "Saved observation path is not absolute")
        path = os.path.normcase(os.path.normpath(path))
        key = fingerprint({"source": asdict(source), "path": path})
        prior = self.db.execute(
            "SELECT r.* FROM resource_observations o JOIN resources r ON r.id=o.resource "
            "WHERE o.source_key=? AND r.environment=? AND r.agent=? LIMIT 1",
            (key, access.environment_id, access.agent_id),
        ).fetchone()
        if prior:
            return self._record(prior)
        safe = self.store.redactor.value({k: v for k, v in facts.items() if k in FACTS})
        value = ResourceRevision(
            Path(path).name or path,
            path,
            "directory" if safe.get("kind") == "directory" else "file",
        )
        record = self._save(access, value)
        if record.status != "active":
            return record
        observation = ResourceObservation(
            str(uuid4()), record.id, path, safe, source, observed_at or utc_now().isoformat()
        )
        self.db.execute(
            "INSERT OR IGNORE INTO resource_observations VALUES(?,?,?,?)",
            (observation.id, record.id, key, dumps(asdict(observation))),
        )
        if source.run_id:
            self.db.execute(
                "INSERT OR IGNORE INTO resource_links VALUES(?,?,?,?)",
                (record.id, source.run_id, source.sequence or 0, relation),
            )
        return self.read(access, record.id)

    def observe(self, access, path, facts, source):
        self.check(access)
        if source.run_id and self.store.queries().run_scope(source.run_id) != access.scope_id:
            raise ConfigurationError("resource_source_scope_mismatch")
        with self.store.transaction():
            return self._observe(access, path, facts, source)

    def links(self, access, identifier):
        self._row(access, identifier)
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT l.* FROM resource_links l JOIN runs r ON r.id=l.run JOIN sessions s ON s.id=r.scope "
                "WHERE l.resource=? AND s.environment_id=? ORDER BY r.created DESC,l.sequence DESC",
                (identifier, access.environment_id),
            )
        ]

    def audit(self, access, identifier):
        self._row(access, identifier)
        return [
            dict(r)
            for r in self.db.execute(
                "SELECT operation,revision,body,created FROM resource_events WHERE resource=? "
                "AND environment=? AND agent=? ORDER BY id",
                (identifier, access.environment_id, access.agent_id),
            )
        ]

    def process(self, access, *, max_runs=20, max_events=1000):
        self.check(access)
        if not 1 <= max_runs <= 20 or not 1 <= max_events <= 1000:
            raise OperationError("resource_process_limit", "At most 20 Runs and 1000 events")
        jobs = list(
            self.db.execute(
                "SELECT j.run,j.cursor,r.scope FROM resource_jobs j JOIN runs r ON r.id=j.run "
                "JOIN sessions s ON s.id=r.scope WHERE s.environment_id=? AND r.state!='running' "
                "AND j.state!='done' ORDER BY r.created,r.id LIMIT ?",
                (access.environment_id, max_runs),
            )
        )
        count = processed = 0
        for job in jobs:
            if count >= max_events:
                break
            try:
                with self.store.transaction():
                    # Re-read under write lock so another processor cannot move the cursor backward.
                    cursor = self.db.execute(
                        "SELECT cursor FROM resource_jobs WHERE run=?", (job["run"],)
                    ).fetchone()[0]
                    rows = list(
                        self.db.execute(
                            "SELECT sequence,body FROM events WHERE run=? AND sequence>? ORDER BY sequence LIMIT ?",
                            (job["run"], cursor, max_events - count),
                        )
                    )
                    for row in rows:
                        event = json.loads(row["body"])
                        source = ResourceSource(
                            "tool",
                            job["run"],
                            row["sequence"],
                            event.get("payload", {}).get("call_id"),
                        )
                        for path, facts, relation in observations(event):
                            self._observe(
                                access, path, facts, source, relation, event.get("occurred_at")
                            )
                        cursor = row["sequence"]
                    more = self.db.execute(
                        "SELECT 1 FROM events WHERE run=? AND sequence>? LIMIT 1",
                        (job["run"], cursor),
                    ).fetchone()
                    self.db.execute(
                        "UPDATE resource_jobs SET cursor=?,state=?,error=NULL WHERE run=?",
                        (cursor, "pending" if more else "done", job["run"]),
                    )
                    count += len(rows)
                    processed += 1
            except Exception as exc:
                self.db.execute(
                    "UPDATE resource_jobs SET error=? WHERE run=?", (type(exc).__name__, job["run"])
                )
                raise
        return {"runs": processed, "events": count, "pending": self.pending(access)}

    def pending(self, access):
        self.check(access)
        return self.db.execute(
            "SELECT count(*) FROM resource_jobs j JOIN runs r ON r.id=j.run JOIN sessions s ON s.id=r.scope "
            "WHERE s.environment_id=? AND j.state!='done'",
            (access.environment_id,),
        ).fetchone()[0]

    def rebuild(self, access):
        self.check(access)
        with self.store.transaction():
            for row in list(
                self.db.execute(
                    "SELECT * FROM resources WHERE environment=? AND agent=?",
                    (access.environment_id, access.agent_id),
                )
            ):
                self._index(row["id"], self._record(row).content)

    def software(self, access, identifier, *, management=False):
        record = self.read(access, identifier, management=management)
        row = self.db.execute(
            "SELECT body FROM software WHERE resource=?", (identifier,)
        ).fetchone()
        if row is None:
            raise OperationError(
                "software_not_registered", "Register and verify this executable first"
            )
        cfg = SoftwareSpec(**json.loads(row[0]))
        if not management and (
            record.content.kind != "software" or cfg.executable != record.content.path
        ):
            raise OperationError(
                "software_changed", "Location changed; verify the registration again"
            )
        if not management and not cfg.enabled:
            raise OperationError("software_disabled", "Software execution is not enabled")
        return record, cfg

    def save_software(self, access, identifier, revision, cfg):
        self.check(access)
        with self.store.transaction():
            record = self.read(access, identifier, management=True)
            value = replace(record.content, kind="software")
            if cfg.resource_id != identifier or cfg.executable != value.path:
                raise OperationError(
                    "software_location_mismatch", "Verified executable differs from record"
                )
            record = self._revise(access, identifier, revision, value, "active")
            self.db.execute(
                "INSERT INTO software VALUES(?,?) ON CONFLICT(resource) DO UPDATE SET body=excluded.body",
                (identifier, dumps(asdict(cfg))),
            )
            self._event(access, identifier, "software_verified", record.revision, asdict(cfg))
            return record
