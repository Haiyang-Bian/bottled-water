"""Shared metadata/hash algorithms, without importing a process host or event loop."""

import hashlib
from pathlib import Path
import time

from agent_contracts.errors import OperationError


async def cooperate():
    import asyncio

    await asyncio.sleep(0)


def check(context):
    context.check()
    if time.monotonic() >= context.deadline:
        raise OperationError("operation_timeout", "Resource operation deadline expired")


async def probe(path, context, *, hash_limit=64 * 1024 * 1024, metadata_only=False,
                checkpoint=cooperate):
    check(context)
    path = Path(path)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return {"exists": False, "observation_status": "observed"}
    facts = {
        "exists": True,
        "kind": "directory" if path.is_dir() else "file",
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "observation_status": "observed",
        "hash_status": "not_requested",
    }
    if facts["kind"] == "file" and not metadata_only:
        if hash_limit is not None and stat.st_size > hash_limit:
            facts["hash_status"] = "size_limit"
        else:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(256 * 1024):
                    check(context)
                    digest.update(block)
                    await checkpoint()
            latest = path.stat()
            if (stat.st_size, stat.st_mtime_ns, stat.st_ino) != (
                latest.st_size, latest.st_mtime_ns, latest.st_ino):
                raise OperationError("resource_changed", "File changed during verification; retry")
            facts.update(sha256=digest.hexdigest(), hash_status="complete")
    check(context)
    return facts
