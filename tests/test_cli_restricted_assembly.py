"""Reject incomplete/foreign restricted composition before any model or filesystem use."""

from types import SimpleNamespace

import pytest

from agent_cli.execution_bindings import ExecutionBindings
from agent_cli.host import run_turn
from agent_contracts.errors import ConfigurationError
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_contracts.permissions import PathPermission, StandingPermissionPolicy
from agent_subsystems.workspaces.permissions import freeze_policy


def bindings(path):
    snapshot = freeze_policy(StandingPermissionPolicy("environment", "local", 1,
        (PathPermission(path, "read"),), enabled=True))
    return ExecutionBindings(ResourceGrant(WorkspaceSpec((path,)), frozenset({"files", "process"}),
        "windows_lpac", snapshot), ExecutionLocation(path),
        SimpleNamespace(capabilities={"filesystem_isolation": True, "network_isolation": True}),
        object(), object(), object(), {}, {})


@pytest.mark.parametrize("port", ["file_operations", "authorization", "software", "executables"])
async def test_incomplete_restricted_ports_never_fall_back(tmp_path, port):
    execution = bindings(tmp_path)
    setattr(execution, port, None)
    with pytest.raises(ConfigurationError, match="every controlled operation port"):
        await run_turn(None, {}, None, None, {}, None, "task", execution=execution)


async def test_foreign_restricted_policy_rejected_before_model(tmp_path):
    execution = bindings(tmp_path)
    store = SimpleNamespace(environment=SimpleNamespace(environment_id="other", default_agent_id="local"))
    with pytest.raises(ConfigurationError, match="another environment"):
        await run_turn(store, {}, None, None, {}, None, "task", execution=execution)
