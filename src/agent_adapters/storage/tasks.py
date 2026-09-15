"""Environment-bound task summaries over original records, including old schemas."""

import json

from agent_contracts.errors import ConfigurationError, OperationError
from agent_subsystems.workspaces.task_queries import query_parts, matches_date, text_score
from .session_queries import decode


class TaskCatalog:
    def __init__(self, store):
        self.store = store

    def check(self, access=None):
        self.store._check_environment()
        if access:
            env = self.store.environment
            if not env or (access.environment_id, access.agent_id) != (
                env.environment_id,
                env.default_agent_id,
            ):
                raise ConfigurationError("task_identity_mismatch")

    def exists(self, access, identifier):
        self.check(access)
        return self.store.queries().session(identifier) is not None

    def rows(self, query="", *, root=None, since=None, until=None, now=None, access=None):
        self.check(access)
        query, bounds = query_parts(query, since=since, until=until, now=now)
        rows = self.store.queries().catalog(root)
        selected = []
        for row in rows:
            if not matches_date(row["last_active"], bounds):
                continue
            score = 0
            if query:
                requests = self.store.db.execute(
                    "SELECT request FROM runs WHERE scope=?", (row["id"],)
                )
                text = (
                    row["cwd"]
                    + " "
                    + " ".join(str(decode(r[0]).get("input", "")) for r in requests)
                )
                if self.store.schema_version >= 5:
                    resources = self.store.db.execute(
                        "SELECT DISTINCT rr.body FROM resource_links l JOIN runs r ON r.id=l.run "
                        "JOIN resources z ON z.id=l.resource JOIN resource_revisions rr "
                        "ON rr.resource=z.id AND rr.revision=z.revision WHERE r.scope=? "
                        "AND z.environment=? AND z.agent=? AND z.status='active'",
                        (
                            row["id"],
                            self.store.environment.environment_id,
                            self.store.environment.default_agent_id,
                        ),
                    )
                    text += " " + " ".join(r[0] for r in resources)
                score = text_score(text, query)
                if not score:
                    continue
            selected.append((score, row))
        # Input catalog already has the stable last-run ordering; score ties preserve it.
        selected.sort(key=lambda item: -item[0])
        return [row for _, row in selected]

    def search(self, access, query="", *, since=None, until=None, offset=0, limit=20):
        self.check(access)
        if offset < 0 or not 1 <= limit <= 20:
            raise OperationError("task_query_limit", "Page limit is 1–20")
        rows = self.rows(query, since=since, until=until, access=access)
        return [self.brief(row) for row in rows[offset : offset + limit]]

    def brief(self, row):
        data = {
            "id": row["id"],
            "title": str(decode(row["first_request"]).get("input", ""))[:200],
            "cwd": row["cwd"],
            "last_active": row["last_active"],
            "state": row["state"],
            "last_run_id": row["last_run_id"],
            "run_count": row["run_count"],
        }
        return self.store.redactor.value(data)

    def read(self, access, identifier):
        self.check(access)
        queries = self.store.queries()
        if queries.session(identifier) is None:
            raise OperationError("task_not_found", "No accessible task")
        result = {
            "id": identifier,
            "runs": [],
            "notice": "Saved observations and assistant statements; not task history transfer",
        }
        for row in queries.runs(identifier, limit=3):
            operations = {}
            count = 0
            for event in queries.events(identifier, row["id"]):
                payload = event.get("payload", {})
                cid = payload.get("call_id")
                if event.get("type") == "agent.tool_started" and cid:
                    operations[cid] = {
                        "call_id": cid,
                        "tool": payload.get("tool"),
                        "state": "unknown",
                        "sequence": event.get("sequence"),
                        "declared_outputs": payload.get("parameters", {}).get("outputs", []),
                    }
                if event.get("type") == "agent.tool_result" and cid:
                    data = payload.get("result")
                    facts = {
                        k: data[k]
                        for k in ("path", "sha256", "exit_code", "outputs", "error_code")
                        if isinstance(data, dict) and k in data
                    }
                    operations[cid] = {
                        "call_id": cid,
                        "tool": payload.get("tool"),
                        "state": "completed" if payload.get("success") is True else "failed",
                        "facts": facts,
                        "sequence": event["sequence"],
                    }
                while len(operations) > 20:
                    del operations[next(iter(operations))]
                    count += 1
            saved = decode(row["result"])
            request = str(decode(row["request"]).get("input", ""))
            statement = str(saved.get("output", ""))
            turn = {
                "run_id": row["id"],
                "created": row["created"],
                "state": row["state"],
                "reason_code": saved.get("reason_code", "unknown"),
                "request": request[:1200],
                "request_truncated": len(request) > 1200,
                "assistant_statement": statement[:1600],
                "assistant_statement_truncated": len(statement) > 1600,
                "operations": list(operations.values()),
                "operations_omitted": count,
            }
            if self.store.schema_version >= 5:
                turn["resources"] = [
                    dict(r)
                    for r in self.store.db.execute(
                        "SELECT l.resource,l.sequence,l.relation FROM resource_links l JOIN resources r "
                        "ON r.id=l.resource WHERE l.run=? AND r.environment=? AND r.agent=? "
                        "AND r.status='active' ORDER BY l.sequence DESC LIMIT 20",
                        (row["id"], access.environment_id, access.agent_id),
                    )
                ]
            result["runs"].append(turn)
        while len(json.dumps(result, ensure_ascii=False)) > 7800:
            turns = result["runs"]
            if not turns:
                break
            oldest = turns[-1]
            if oldest["operations"]:
                oldest["operations"].pop(0)
                oldest["operations_omitted"] += 1
            elif oldest["assistant_statement"]:
                oldest["assistant_statement"] = ""
            else:
                turns.pop()
            result["truncated"] = True
        return self.store.redactor.value(result)
