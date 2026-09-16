"""Owned subprocess fixture: exercise the real host while its input stays open."""

import asyncio
import json
import os
from pathlib import Path
import sys

from agent_adapters.local.dependencies import DependencyManifest
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.permission_host import PermissionHost
from agent_subsystems.workspaces.permissions import freeze_policy


async def main():
    store = SQLiteStore(Path(sys.argv[1]))
    host = PermissionHost(store)
    host.start()
    try:
        prepared = host.prepare(freeze_policy(host.authority.load()),
                                DependencyManifest.capture(Path(sys.argv[2])))
        print(json.dumps({"host": host.id, "generation": prepared.generation}), flush=True)
        while True:
            line = await asyncio.to_thread(sys.stdin.readline)
            operation = line.strip()
            if not line or operation == "stop":
                return
            if operation == "crash":
                os._exit(41)  # Fault injection in this fixture's actual Python process.
            if operation == "busy":
                host.authority.register("fixture-run", host.id, prepared.generation, prepared.snapshot)
                host.running = True
            elif operation == "idle":
                host.authority.finish("fixture-run", cleaned=True)
                host.running = False
            elif operation == "inspect":
                print(json.dumps({"running": host.running, "instances": len(host.instances),
                                  "revision": host.authority.load().revision}), flush=True)
                continue
            else:
                raise ValueError("Unknown test action")
            print(json.dumps({"ok": True}), flush=True)
    finally:
        await host.close()
        store.close()


if __name__ == "__main__":
    asyncio.run(main())
