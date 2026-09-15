"""Read models over the existing local schema; no initialization or writes."""

import json


def decode(value, default=None):
    try:
        return json.loads(value) if value else ({} if default is None else default)
    except (TypeError, ValueError):
        return {} if default is None else default


class SessionQueries:
    def __init__(self, db, environment=None):
        self.db = db
        self.environment = environment
        self.version = db.execute("PRAGMA user_version").fetchone()[0]

    def _scope(self, alias="s"):
        if self.version >= 3:
            if self.environment is None:
                raise ValueError("Verified environment required")
            return f"{alias}.environment_id=?", [self.environment.environment_id]
        return "1=1", []

    def catalog(self, root=None):
        # Only sessions with runs participate. Opening a session is not activity.
        scope, args = self._scope()
        position = "s.cwd" if self.version >= 3 else "s.root"
        columns = ("s.origin_root,s.cwd,s.workspace_version,s.environment_id"
                   if self.version >= 3 else
                   "s.root origin_root,s.root cwd,0 workspace_version,NULL environment_id")
        where = f"WHERE {scope}"
        if root is not None:
            where += f" AND {position}=?"
            args.append(str(root))
        query = f"""
        SELECT s.id,{columns},count(r.id) run_count,
          (SELECT request FROM runs WHERE scope=s.id ORDER BY created,id LIMIT 1) first_request,
          (SELECT request FROM runs WHERE scope=s.id ORDER BY created DESC,id DESC LIMIT 1)
            last_request,
          (SELECT created FROM runs WHERE scope=s.id ORDER BY created DESC,id DESC LIMIT 1)
            last_active,
          (SELECT id FROM runs WHERE scope=s.id ORDER BY created DESC,id DESC LIMIT 1) last_run_id,
          (SELECT state FROM runs WHERE scope=s.id ORDER BY created DESC,id DESC LIMIT 1) state,
          (SELECT result FROM runs WHERE scope=s.id ORDER BY created DESC,id DESC LIMIT 1) result
        FROM sessions s JOIN runs r ON r.scope=s.id {where}
        GROUP BY s.id ORDER BY last_active DESC,last_run_id DESC,s.id DESC
        """
        rows = self.db.execute(query, args).fetchall()
        return [dict(row) for row in rows]

    def session(self, identifier):
        scope, args = self._scope()
        row = self.db.execute(
            f"SELECT * FROM sessions s WHERE s.id=? AND {scope}", [identifier, *args]
        ).fetchone()
        if row is None:
            return None
        if self.version >= 3:
            return {**dict(row), "granted_roots": decode(row["granted_roots"], [])}
        return {"id": row["id"], "environment_id": None, "origin_root": row["root"],
                "cwd": row["root"], "granted_roots": [row["root"], *decode(row["dirs"], [])],
                "workspace_version": 0, "created": row["created"], "updated": row["updated"]}

    def run_scope(self, identifier):
        scope, args = self._scope()
        row = self.db.execute(
            f"SELECT r.scope FROM runs r JOIN sessions s ON r.scope=s.id WHERE r.id=? AND {scope}",
            [identifier, *args],
        ).fetchone()
        return row[0] if row else None

    def runs(self, scope, *, before=None, limit=3):
        if self.session(scope) is None:
            return []
        args = [scope]
        clause = ""
        if before:
            clause = " AND (created,id)<(?,?)"
            args.extend(before)
        args.append(limit)
        rows = self.db.execute(
            "SELECT id,created,state,request,result FROM runs WHERE scope=?" + clause
            + " ORDER BY created DESC,id DESC LIMIT ?", args,
        ).fetchall()
        return [dict(row) for row in rows]

    def events(self, scope, run_id):
        if self.session(scope) is None:
            return
        # Scope is checked in SQL, including when the caller supplies a Run ID.
        cursor = self.db.execute(
            "SELECT e.body FROM events e JOIN runs r ON e.run=r.id "
            "WHERE r.scope=? AND r.id=? ORDER BY e.sequence", (scope, run_id),
        )
        for row in cursor:
            yield decode(row[0])
