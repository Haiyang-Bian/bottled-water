"""Consistent SQLite v1 backup and exclusive, transactional v2 migration."""

import sqlite3
from contextlib import ExitStack
from datetime import datetime, timezone

from .session_lock import SessionLock


def migrate_v1(db, path):
    # The caller holds the migration gate. Keep every existing session lock until commit.
    with ExitStack() as locks:
        for row in db.execute("SELECT id FROM sessions ORDER BY id"):
            locks.enter_context(SessionLock(path.parent / "locks", row[0], guard=False))
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = path.with_name(path.name + f".v1-{stamp}.bak")
        with sqlite3.connect(backup_path) as backup:
            db.backup(backup)
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(
                "CREATE TABLE continuation_metadata(scope TEXT PRIMARY KEY, body TEXT NOT NULL)"
            )
            db.execute("PRAGMA user_version=2")
            db.commit()
        except BaseException:
            db.rollback()
            raise
        return backup_path
