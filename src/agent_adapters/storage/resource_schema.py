"""Schema v5: resource facts and software approvals; no automatic memory approval."""

RESOURCE_SCHEMA = (
    "CREATE TABLE resources(id TEXT PRIMARY KEY,environment TEXT NOT NULL,agent TEXT NOT NULL,"
    "revision INTEGER NOT NULL,status TEXT NOT NULL,path TEXT NOT NULL,"
    "UNIQUE(environment,agent,path))",
    "CREATE TABLE resource_revisions(resource TEXT NOT NULL REFERENCES resources(id),"
    "revision INTEGER NOT NULL,body TEXT NOT NULL,PRIMARY KEY(resource,revision))",
    "CREATE TABLE resource_observations(id TEXT PRIMARY KEY,resource TEXT NOT NULL REFERENCES "
    "resources(id),source_key TEXT NOT NULL,body TEXT NOT NULL,UNIQUE(resource,source_key))",
    "CREATE INDEX resource_observation_resource ON resource_observations(resource)",
    "CREATE TABLE resource_links(resource TEXT NOT NULL REFERENCES resources(id),run TEXT NOT NULL "
    "REFERENCES runs(id),sequence INTEGER NOT NULL,relation TEXT NOT NULL,"
    "PRIMARY KEY(resource,run,sequence,relation))",
    "CREATE TABLE resource_terms(resource TEXT NOT NULL REFERENCES resources(id),term TEXT NOT NULL,"
    "PRIMARY KEY(resource,term))",
    "CREATE INDEX resource_term_lookup ON resource_terms(term,resource)",
    "CREATE TABLE resource_jobs(run TEXT PRIMARY KEY REFERENCES runs(id),state TEXT NOT NULL,"
    "cursor INTEGER NOT NULL DEFAULT 0,error TEXT)",
    "CREATE TABLE software(resource TEXT PRIMARY KEY REFERENCES resources(id),body TEXT NOT NULL)",
    "CREATE TABLE resource_events(id INTEGER PRIMARY KEY AUTOINCREMENT,environment TEXT NOT NULL,"
    "agent TEXT NOT NULL,resource TEXT,operation TEXT NOT NULL,revision INTEGER,body TEXT NOT NULL,"
    "created TEXT NOT NULL)",
)
