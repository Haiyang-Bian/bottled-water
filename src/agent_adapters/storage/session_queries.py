"""Read models over the existing local schema; no initialization or writes."""

import json


def decode(value, default=None):
    try:
        return json.loads(value) if value else ({} if default is None else default)
    except (TypeError, ValueError):
        return {} if default is None else default


class SessionQueries:
    def __init__(self, db):
        self.db = db

    def catalog(self, root=None):
        # Only sessions with runs participate. Opening a session is not activity.
        where = "WHERE s.root=?" if root is not None else ""
        query = f"""
        SELECT s.id,s.root,s.dirs,count(r.id) run_count,
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
        rows = self.db.execute(query, (str(root),) if root is not None else ()).fetchall()
        return [dict(row) for row in rows]

    def session(self, identifier):
        row = self.db.execute("SELECT * FROM sessions WHERE id=?", (identifier,)).fetchone()
        return {**dict(row), "dirs": decode(row["dirs"], [])} if row else None

    def run_scope(self, identifier):
        row = self.db.execute("SELECT scope FROM runs WHERE id=?", (identifier,)).fetchone()
        return row[0] if row else None

    def runs(self, scope, *, before=None, limit=3):
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
        # Scope is checked in SQL, including when the caller supplies a Run ID.
        cursor = self.db.execute(
            "SELECT e.body FROM events e JOIN runs r ON e.run=r.id "
            "WHERE r.scope=? AND r.id=? ORDER BY e.sequence", (scope, run_id),
        )
        for row in cursor:
            yield decode(row[0])
