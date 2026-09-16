"""Fixture-only backend for explicit withdrawal. Not a production ACL adapter.

The caller owns the entire fresh Tree, including the ungranted Private directory.
No user directory is accepted. The production adapter still needs persistent
ownership, cross-host coordination and recovery before it can use this protocol.
"""

import json
from pathlib import Path
import time

from .native import system_capability_sids
from .standing import MODIFY, READ_EXECUTE, add_aces, cleanup_tree, describe, fixture_acl, tree


class FixturePreparationBackend:
    def __init__(self, root, report, save, *, dependencies=None):
        self.root, self.report, self.save = Path(root), report, save
        self.dependencies = dependencies
        self.records = {}
        self.failure = None

    @staticmethod
    def identity(path):
        info = path.stat()
        return (info.st_dev, info.st_ino)

    def prepare(self, generation, snapshot):
        from agent_subsystems.workspaces.permissions import inheritable_roots

        if generation in self.records:
            raise RuntimeError("Generation already used")
        roots = inheritable_roots(snapshot)
        if any(not rule.path.is_relative_to(self.root) for rule in roots):
            raise RuntimeError("Permission outside owned fixture")
        started = time.perf_counter()
        inspected = list(tree(self.root))  # Reject all aliases before the first mutation.
        sid = system_capability_sids("AgentHub.Probe.Policy." + generation)[0]
        record = {"generation": generation, "sid": sid, "state": "preparing", "grants": [],
                  "prepare_scanned_objects": len(inspected), "verifications": [],
                  "roots": [{"path": str(rule.path), "identity": self.identity(rule.path)}
                            for rule in roots]}
        self.records[generation] = record
        self.report.setdefault("preparations", []).append(record)
        self.save()

        def grant(path, entries):
            intent = {"path": str(path), "identity": self.identity(path), "entries": entries,
                      "state": "intent"}
            record["grants"].append(intent)
            self.save()
            add_aces(path, sid, entries)
            intent["state"] = "applied"
            self.save()
            if self.failure == "prepare":
                raise RuntimeError("Injected failure after first ACL mutation")

        for rule in roots:
            writable = rule.access == "modify"
            grant(rule.path, [(0, 0, MODIFY & ~0x10000), (0, 11, MODIFY)] if writable
                  else [(0, 3, READ_EXECUTE)])
            # A pre-existing inheritance barrier must not be changed or silently ignored.
            # Explicitly prepare missing descendants within this owned fixture only.
            for path in tree(rule.path):
                record["prepare_scanned_objects"] += 1
                if path != rule.path and not describe(path, sid)["policy_aces"]:
                    grant(path, [(0, 3 if path.is_dir() else 0,
                                  MODIFY if writable else READ_EXECUTE)])
        record["state"] = "prepared"
        record["prepare_seconds"] = time.perf_counter() - started
        self.save()

    def verify(self, generation):
        started = time.perf_counter()
        record = self.records[generation]
        if record["state"] != "prepared":
            raise RuntimeError("Fixture preparation is not ready")
        if self.dependencies is not None:
            self.dependencies.verify()
        for item in record["roots"]:
            if self.identity(Path(item["path"])) != tuple(item["identity"]):
                raise RuntimeError("Root object changed; repair required")
            if not describe(Path(item["path"]), record["sid"])["policy_aces"]:
                raise RuntimeError("Prepared root ACL disappeared")
        record["verifications"].append({"seconds": time.perf_counter() - started,
                                         "root_checks": len(record["roots"]),
                                         "business_recursive_scans": 0,
                                         "dependency_objects_rechecked": len(json.loads(
                                             self.dependencies.encoded))
                                         if self.dependencies is not None else 0})
        self.save()

    def retire(self, generation):
        record = self.records.get(generation)
        if record is None or record["state"] == "retired":
            return
        record["state"] = "retiring"
        self.save()
        if self.failure == "retire":
            record["state"] = "repair_required"
            self.save()
            raise RuntimeError("Injected cleanup verification failure")
        for item in record["roots"]:
            if self.identity(Path(item["path"])) != tuple(item["identity"]):
                record["state"] = "repair_required"
                self.save()
                raise RuntimeError("Root moved before withdrawal; no guessed cleanup")
        before = {str(path): fixture_acl(path) for path in tree(self.root)}
        record["cleanup_objects"] = cleanup_tree(self.root, {record["sid"]})
        preserved = all(fixture_acl(Path(path)) == {
            **acl, "aces": [entry for entry in acl["aces"] if entry[-1] != record["sid"]]
        } for path, acl in before.items())
        if not preserved:
            record["state"] = "repair_required"
            self.save()
            raise RuntimeError("Unrelated fixture ACL changed during withdrawal")
        record.update(state="retired", cleanup_verified=True, unrelated_acl_preserved=True)
        self.save()
