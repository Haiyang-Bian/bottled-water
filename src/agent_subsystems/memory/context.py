"""Retrieve once per Run, recheck selected revisions immediately before each request."""

import json
from dataclasses import replace

from agent_contracts.errors import ConfigurationError, OperationError


class RunMemoryContext:
    def __init__(self, reader, access, query, cwd):
        self.reader, self.access, self.query, self.cwd = reader, access, query, cwd
        self.selected = None
        self.diagnostics = {}

    def items(self):
        if self.selected is None:
            try:
                selection = self.reader.select(self.access, self.query, self.cwd)
                self.selected = selection.items
                self.diagnostics = selection.diagnostics
            except ConfigurationError:
                raise  # Identity failures never degrade into a model request.
            except Exception:
                self.selected = []
                self.diagnostics = {
                    "retrieval": "unavailable",
                    "notice": "Optional memory retrieval failed",
                }
        return self.reader.revalidate(self.access, self.selected)

    def filter_results(self, messages):
        result = []
        used = []
        for message in messages:
            if message.role != "tool":
                result.append(message)
                continue
            try:
                value = json.loads(message.content)
            except (ValueError, TypeError):
                result.append(message)
                continue
            data = value.get("result") if isinstance(value, dict) else None
            if not isinstance(data, dict) or "memory_records" not in data:
                result.append(message)
                continue
            allowed = []
            for record in data["memory_records"]:
                try:
                    current = self.reader.read(self.access, record["id"])
                    if current.revision != record["revision"]:
                        continue
                except OperationError:
                    continue
                allowed.append(record)
                used.append({"id": record["id"], "revision": record["revision"], "via": "tool"})
            data["memory_records"] = allowed
            data["notice"] = "Only still-approved revisions retained; no file authority granted"
            result.append(replace(message, content=json.dumps(value, ensure_ascii=False)))
        return result, used
