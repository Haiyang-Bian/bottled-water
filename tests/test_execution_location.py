"""Working location is an input to each execution, not ambient process state."""

import asyncio
import os
import time
from types import SimpleNamespace

import pytest

from agent_adapters.local.files import LocalFiles
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.tools import LocalToolExecutor, TrustAuthorization
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_contracts.errors import OperationError
from agent_runtime.core.types import ToolCall
from agent_runtime.runtime.adapter_isolation import RunLease
from agent_runtime.runtime.cancellation import CancellationScope
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory, effective_roots, resolve_resource


async def test_parallel_locations_and_command_override_do_not_change_host(tmp_path):
    a, b = tmp_path / "甲 A", tmp_path / "乙 B"
    a.mkdir()
    b.mkdir()
    a, b = canonical_directory(a), canonical_directory(b)
    (a / "note.txt").write_text("A", encoding="utf-8")
    (b / "note.txt").write_text("B", encoding="utf-8")
    store = SQLiteStore(tmp_path / "state.sqlite3")
    for path in (a, b):
        store.trust(path)
    driver = LocalProcessDriver(Redactor())
    workspace = WorkspaceSpec((a, b))
    grant = ResourceGrant(workspace, frozenset({"files", "process"}))
    original = os.getcwd()

    def executor(path):
        request = SimpleNamespace(run_id=str(path), context_scope_id="scope",
                                  agent=SimpleNamespace(id="local"),
                                  metadata={"execution_deadline": time.monotonic() + 60})
        return LocalToolExecutor(grant, ExecutionLocation(path, 7), TrustAuthorization(store),
                                 driver, Redactor()).bind_execution(
            request, CancellationScope(), RunLease(str(path))
        )

    x, y = executor(a), executor(b)
    try:
        results = await asyncio.gather(*[
            ex.execute(ToolCall("file.read", {"path": "note.txt"}, str(i)))
            for i, ex in enumerate((x, y))
        ])
        assert [r.result["content"] for r in results] == ["A", "B"]
        assert [r.result["execution"]["default_cwd"] for r in results] == [str(a), str(b)]
        command = await x.execute(ToolCall("powershell.run", {
            "script": "(Get-Location).Path", "cwd": str(b),
        }, "cmd"))
        assert command.success and command.result["execution"]["cwd"] == str(b)
        again = await x.execute(ToolCall("file.read", {"path": "note.txt"}, "read"))
        assert again.result["content"] == "A" and os.getcwd() == original
        store.trust(a, False)
        denied = await x.execute(ToolCall("file.read", {"path": "note.txt"}, "denied"))
        assert not denied.success
    finally:
        await driver.aclose()
        store.close()


async def test_location_does_not_authorize_outside_path(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    a, b = canonical_directory(a), canonical_directory(b)
    location = ExecutionLocation(b)
    with pytest.raises(OperationError, match="explicitly"):
        await LocalFiles(WorkspaceSpec((a,)), location).write("x", "denied", "new")
    assert not (b / "x").exists()
    roots, inactive = effective_roots([str(a), str(b), str(tmp_path / "gone")], lambda p: p == a)
    assert roots == (a,)
    assert {i["reason"] for i in inactive} == {"not_trusted", "unavailable"}
    with pytest.raises(OperationError, match="Drive-relative"):
        resolve_resource(WorkspaceSpec((a,)), ExecutionLocation(a), "C:relative")
