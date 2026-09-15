"""Optional resource references retrieved once, revalidated for each actual model request."""

import json
from dataclasses import asdict, replace

from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.resources import ResourceContextItem


class RunResourceContext:
    def __init__(self, reader, access, query, tasks=None):
        self.reader, self.access, self.query = reader, access, query
        self.tasks = tasks
        self.selected = None
        self.diagnostics = {}

    def items(self):
        if self.selected is None:
            self.selected = []
            size = 0
            try:
                for record in self.reader.search(self.access, self.query, limit=10):
                    text = json.dumps(asdict(record), ensure_ascii=False)
                    if size + len(text) > 4000:
                        self.diagnostics["selection_truncated"] = True
                        continue
                    size += len(text)
                    self.selected.append(
                        ResourceContextItem(
                            record.id,
                            record.revision,
                            record.observation.id if record.observation else None,
                            text,
                        )
                    )
            except ConfigurationError:
                raise
            except Exception:
                self.selected = []
                self.diagnostics["retrieval"] = "unavailable"
        allowed = []
        for item in self.selected:
            try:
                record = self.reader.read(self.access, item.resource_id)
            except OperationError:
                continue
            observation = record.observation.id if record.observation else None
            if record.revision == item.revision and observation == item.observation_id:
                allowed.append(item)
        return allowed

    def filter_results(self, messages):
        result, used, tasks = [], [], []
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
            if not isinstance(data, dict):
                result.append(message)
                continue
            if "resource_records" in data:
                allowed = []
                for saved in data["resource_records"]:
                    try:
                        current = self.reader.read(self.access, saved["id"])
                        if current.revision != saved["revision"]:
                            continue
                        if "software" in saved:
                            self.reader.software(self.access, saved["id"])
                    except OperationError:
                        continue
                    allowed.append(saved)
                    used.append(
                        {
                            "id": saved["id"],
                            "revision": saved["revision"],
                            "observation_id": (saved.get("observation") or {}).get("id"),
                            "via": "tool",
                        }
                    )
                data["resource_records"] = allowed
            if "task_records" in data:
                for task in data["task_records"]:
                    if not self.tasks or not self.tasks.exists(self.access, task["id"]):
                        raise ConfigurationError("task_identity_mismatch")
                    tasks.append(
                        {"id": task["id"], "runs": [r["run_id"] for r in task.get("runs", [])]}
                    )
            result.append(replace(message, content=json.dumps(value, ensure_ascii=False)))
        return result, used, tasks
