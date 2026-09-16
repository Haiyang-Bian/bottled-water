"""Local-only v6 authority, recovery intents and host coordination."""

PERMISSION_SCHEMA = (
    "CREATE TABLE permission_policies(agent TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
    "body TEXT NOT NULL)",
    "CREATE TABLE task_permissions(session TEXT PRIMARY KEY REFERENCES sessions(id), "
    "mode TEXT NOT NULL, selection TEXT NOT NULL)",
    "CREATE TABLE permission_hosts(id TEXT PRIMARY KEY, agent TEXT NOT NULL, body TEXT NOT NULL, "
    "state TEXT NOT NULL)",
    "CREATE TABLE permission_preparations(id TEXT PRIMARY KEY, host TEXT NOT NULL "
    "REFERENCES permission_hosts(id), agent TEXT NOT NULL, snapshot TEXT NOT NULL, "
    "body TEXT NOT NULL, state TEXT NOT NULL)",
    "CREATE TABLE permission_leases(run TEXT PRIMARY KEY, host TEXT NOT NULL "
    "REFERENCES permission_hosts(id), preparation TEXT NOT NULL "
    "REFERENCES permission_preparations(id), snapshot TEXT NOT NULL, state TEXT NOT NULL)",
    "CREATE TABLE permission_transitions(id TEXT PRIMARY KEY, agent TEXT NOT NULL, "
    "expected_revision INTEGER NOT NULL, target TEXT NOT NULL, body TEXT NOT NULL, state TEXT NOT NULL)",
    "CREATE UNIQUE INDEX permission_active_transition ON permission_transitions(agent) "
    "WHERE state IN ('freezing','retiring','repair_required')",
    "CREATE TABLE permission_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT, "
    "created TEXT NOT NULL, operation TEXT NOT NULL, body TEXT NOT NULL)",
)
