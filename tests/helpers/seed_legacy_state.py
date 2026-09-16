"""Run with an independently installed old wheel to create authentic old state."""

import asyncio
import json
import sys
from pathlib import Path

from agent_adapters.credentials.local import LocalCredentialStore
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.config import save_config
from agent_runtime.core.run_types import ContextDelta


async def main():
    home, project = map(Path, sys.argv[1:])
    reference = LocalCredentialStore(home / "credentials").save("upgrade-test-private-key")
    save_config(
        home,
        {
            "default_profile": "legacy",
            "profiles": {
                "legacy": {
                    "provider": "deepseek",
                    "model": "legacy-model",
                    "base_url": "https://api.deepseek.com",
                    "credential_ref": reference,
                }
            },
            "limits": {"wall_time_seconds": 240},
        },
    )
    store = SQLiteStore(home / "state.sqlite3")
    try:
        session = store.new_session(project)
        store.trust(project)
        await store.commit(
            session["id"],
            ContextDelta(
                0,
                {},
                messages=(
                    {"role": "user", "content": "Legacy request"},
                    {"role": "assistant", "content": "Legacy answer"},
                ),
            ),
        )
        # These are persisted legacy facts, not an executed provider acceptance.
        store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (
            "legacy-run", session["id"], "completed", "2026-01-01T00:00:00+00:00",
            json.dumps({"input": "Legacy request"}),
            json.dumps({"output": "Legacy answer", "reason_code": "completed"}), 0,
        ))
        store.db.commit()
        memory_ids = {}
        if store.db.execute("PRAGMA user_version").fetchone()[0] >= 4:
            from agent_adapters.storage.memory import SQLiteMemory
            from agent_contracts.memory import MemoryRevision
            memory = SQLiteMemory(store)
            access = memory.access()
            active = memory.save(access, MemoryRevision("旧偏好", "默认中文", basic=True))
            forgotten = memory.save(access, MemoryRevision("已遗忘", "不可召回"))
            memory.set_status(access, forgotten.id, 1, "forgotten")
            memory_ids = {"active": active.id, "forgotten": forgotten.id}
        resource_id = None
        if store.db.execute("PRAGMA user_version").fetchone()[0] >= 5:
            from agent_adapters.storage.resources import SQLiteResources
            from agent_contracts.resources import ResourceRevision
            resources = SQLiteResources(store)
            resource_id = resources.save(
                resources.access(), ResourceRevision("旧项目", str(project), "project")
            ).id
        print(
            json.dumps(
                {
                    "session_id": session["id"],
                    "credential_ref": reference,
                    "schema": store.db.execute("PRAGMA user_version").fetchone()[0],
                    "memory_ids": memory_ids,
                    "resource_id": resource_id,
                }
            )
        )
    finally:
        store.close()


asyncio.run(main())
