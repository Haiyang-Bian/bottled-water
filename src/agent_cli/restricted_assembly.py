"""Normal CLI assembly of the same controlled ports exercised by P2."""

import sys
from pathlib import Path

from agent_adapters.local.managed_restricted import ManagedRestrictedDriver
from agent_adapters.local.restricted import RestrictedAuthorization
from agent_adapters.local.restricted_software import RestrictedSoftware
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_contracts.errors import OperationError
from agent_subsystems.workspaces.permissions import inheritable_roots
from .execution_bindings import ExecutionBindings
from .sandbox import require_setup


def assemble(controller, host, *, json_mode=False):
    value, manifest = require_setup(controller.home)
    snapshot = controller.permission_snapshot()
    reported = [0]
    def progress(event):
        # Preparation belongs to the host, before the model Run. No stdout pollution.
        reported[0] += 1
        if not json_mode and (reported[0] == 1 or reported[0] % 100 == 0):
            print(f"权限准备 · {event['objects']} 个对象", file=sys.stderr)
    prepared = host.prepare(snapshot, manifest, progress)
    roots = tuple(rule.path for rule in inheritable_roots(snapshot))
    grant = ResourceGrant(WorkspaceSpec(roots), frozenset({"files", "process"}),
                          "windows_lpac", snapshot)
    location = ExecutionLocation(Path(controller.session["cwd"]),
                                 controller.session["workspace_version"])
    driver = ManagedRestrictedDriver(
        prepared, value["bundle"], controller.home / "sandbox" / "runs", value["executables"],
        namespace_experiment=value["namespace"], authority=host.authority, host=host.id,
    )
    from agent_adapters.storage.resources import SQLiteResources
    resources = SQLiteResources(controller.store)
    access = resources.access(scope_id=controller.session["id"])
    mapping = {}
    for row in controller.store.db.execute(
        "SELECT s.resource FROM software s JOIN resources r ON r.id=s.resource "
        "WHERE r.environment=? AND r.agent=? AND r.status='active'",
        (access.environment_id, access.agent_id),
    ):
        try:
            cfg = resources.software(access, row[0])
        except OperationError:
            continue
        item = driver.software.get(cfg.kind)
        if item and cfg.enabled and cfg.sha256 == item["sha256"]:
            mapping[cfg.resource_id] = cfg.kind
    return ExecutionBindings(
        grant, location, driver, driver, RestrictedAuthorization(),
        RestrictedSoftware(driver, mapping), value["executables"],
        {"permission_managed": True, "policy_revision": snapshot.policy.revision,
         "policy_digest": snapshot.digest, "dependency_digest": manifest.digest,
         "driver": "windows_lpac", "network": "deny", "os_build": sys.getwindowsversion().build},
    )
