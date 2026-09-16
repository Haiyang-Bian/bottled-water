"""Resource normalization, relevance and objective observation projection rules."""

import os
from dataclasses import replace
from pathlib import Path

from agent_contracts.errors import OperationError
from agent_subsystems.memory.rules import terms

KINDS = {"file", "directory", "project", "dataset", "artifact", "software"}
FACTS = {
    "exists",
    "kind",
    "size",
    "mtime_ns",
    "sha256",
    "hash_status",
    "truncated",
    "bytes_written",
    "created",
    "replaced",
    "exit_code",
    "observation_status",
}


def location(path):
    """Normalize a supplied local path. Read-only catalog queries never call this."""
    value = Path(path).expanduser()
    if not value.is_absolute():
        raise OperationError("absolute_path_required", "Resource locations must be absolute")
    return os.path.normcase(str(value.resolve()))


def validate(content):
    if not content.name.strip() or len(content.name) > 200 or content.kind not in KINDS:
        raise OperationError("invalid_resource", "Name: 1–200 characters; invalid resource kind")
    if len(content.aliases) > 20 or any(not a.strip() or len(a) > 200 for a in content.aliases):
        raise OperationError("invalid_resource", "At most 20 nonempty aliases of 200 characters")
    return replace(
        content,
        name=content.name.strip(),
        path=location(content.path),
        aliases=tuple(dict.fromkeys(content.aliases)),
    )


def ranking(record, query):
    c = record.content
    query = query.casefold().strip()
    wanted = terms(query)
    names = " ".join((c.name, *c.aliases)).casefold()
    return (
        -int(bool(query) and query in names),
        -len(wanted & terms(names)),
        -int(bool(query) and query in c.path.casefold()),
        -len(wanted & terms(c.path)),
        record.id,
    )


def observations(event):
    """Consume only structured fields from allowlisted successful driver events."""
    if event.get("type") != "agent.tool_result":
        return []
    payload = event.get("payload", {})
    data = payload.get("result")
    if payload.get("success") is not True or not isinstance(data, dict):
        return []
    tool = payload.get("tool")
    if tool in {"file.read", "file.write", "file.edit"}:
        path = data.get("execution", {}).get("path")
        if not path or data.get("path") != path:
            return []
        facts = {k: v for k, v in data.items() if k in FACTS}
        facts.update({"exists": True, "kind": "file", "observation_status": "observed"})
        return [(path, facts, "read" if tool == "file.read" else "modified")]
    if tool in {"software.run", "process.run"}:
        results = []
        for output in data.get("outputs", []):
            after = output.get("after", {})
            if after.get("observation_status") == "observed" and after.get("exists") is True:
                results.append(
                    (
                        output["path"],
                        {k: v for k, v in after.items() if k in FACTS},
                        "observed_output",
                    )
                )
        return results
    return []
