"""Run with the independently installed 0.1.0 Python to create authentic v1 state."""

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
        print(
            json.dumps(
                {
                    "session_id": session["id"],
                    "credential_ref": reference,
                    "schema": store.db.execute("PRAGMA user_version").fetchone()[0],
                }
            )
        )
    finally:
        store.close()


asyncio.run(main())
