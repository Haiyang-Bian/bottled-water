"""Schema v4: local-only memory authority and rebuildable recall index."""

MEMORY_SCHEMA = (
    "CREATE TABLE memories(id TEXT PRIMARY KEY, environment TEXT NOT NULL, agent TEXT NOT NULL, "
    "revision INTEGER NOT NULL, status TEXT NOT NULL, approved INTEGER NOT NULL, "
    "fingerprint TEXT NOT NULL, verified TEXT NOT NULL)",
    "CREATE INDEX memories_owner ON memories(environment,agent,status,approved)",
    "CREATE TABLE memory_revisions(memory TEXT NOT NULL REFERENCES memories(id), "
    "revision INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY(memory,revision))",
    "CREATE TABLE memory_sources(memory TEXT NOT NULL REFERENCES memories(id), "
    "revision INTEGER NOT NULL, source_key TEXT NOT NULL, body TEXT NOT NULL, "
    "PRIMARY KEY(memory,revision,source_key))",
    "CREATE TABLE memory_terms(memory TEXT NOT NULL REFERENCES memories(id), term TEXT NOT NULL, "
    "PRIMARY KEY(memory,term))",
    "CREATE INDEX memory_term_lookup ON memory_terms(term,memory)",
    "CREATE TABLE memory_lineage(memory TEXT NOT NULL REFERENCES memories(id), "
    "source_key TEXT NOT NULL, PRIMARY KEY(memory,source_key))",
    "CREATE TABLE memory_candidates(id TEXT PRIMARY KEY, environment TEXT NOT NULL, "
    "agent TEXT NOT NULL, run TEXT NOT NULL REFERENCES runs(id), call_id TEXT NOT NULL, "
    "revision INTEGER NOT NULL, status TEXT NOT NULL, body TEXT NOT NULL, sources TEXT NOT NULL, "
    "reason TEXT, memory TEXT, UNIQUE(run,call_id))",
    "CREATE TABLE memory_jobs(run TEXT PRIMARY KEY REFERENCES runs(id), state TEXT NOT NULL, "
    "cursor INTEGER NOT NULL DEFAULT 0, error TEXT)",
    "CREATE TABLE memory_suppressed(environment TEXT NOT NULL, agent TEXT NOT NULL, "
    "source_key TEXT NOT NULL, PRIMARY KEY(environment,agent,source_key))",
    "CREATE TABLE memory_events(id INTEGER PRIMARY KEY AUTOINCREMENT, environment TEXT NOT NULL, "
    "agent TEXT NOT NULL, memory TEXT, operation TEXT NOT NULL, revision INTEGER, created TEXT NOT NULL)",
)
