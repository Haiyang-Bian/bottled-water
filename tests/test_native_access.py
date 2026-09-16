"""Native CLI scope is explicit; other hosts keep their bounded defaults."""

import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_adapters.local.file_operations import LocalFileOperations
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.tools import LocalToolExecutor, TrustAuthorization
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.sessions import SessionController
from agent_contracts.errors import OperationError
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_runtime.core.types import ToolCall
from agent_runtime.runtime.adapter_isolation import RunLease
from agent_runtime.runtime.cancellation import CancellationScope
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory, resolve_resource


def bound(store, cwd, driver=None, *, scope="user"):
    grant = ResourceGrant(WorkspaceSpec((cwd,)), frozenset({"files", "process"}),
                          file_access_scope=scope)
    request = SimpleNamespace(run_id="native", context_scope_id="scope",
                              agent=SimpleNamespace(id="local"),
                              metadata={"execution_deadline": time.monotonic() + 60})
    return LocalToolExecutor(grant, ExecutionLocation(cwd), TrustAuthorization(store),
                             driver, Redactor()).bind_execution(
        request, CancellationScope(), RunLease("native"))


async def test_native_file_operations_cross_untrusted_roots_preserve_conflicts(tmp_path):
    a, b = [tmp_path / name for name in ("A", "中文 B")]
    a.mkdir()
    b.mkdir()
    a, b = canonical_directory(a), canonical_directory(b)
    (b / ".git").mkdir()
    path = b / "note.txt"
    path.write_bytes(b"\xef\xbb\xbfhello\r\n")
    store = SQLiteStore(tmp_path / "home/state.sqlite3")
    driver = LocalProcessDriver(Redactor())
    try:
        executor = bound(store, a, driver)
        read = await executor.execute(ToolCall("file.read", {"path": str(path)}, "read"))
        assert read.success and read.result["content"] == "hello\r\n"
        edit = await executor.execute(ToolCall("file.edit", {
            "path": str(path), "old_text": "hello", "new_text": "changed",
            "expected_hash": read.result["sha256"],
        }, "edit"))
        assert edit.success and path.read_bytes() == b"\xef\xbb\xbfchanged\r\n"
        conflict = await executor.execute(ToolCall("file.write", {
            "path": str(path), "content": "bad", "expected_hash": read.result["sha256"],
        }, "conflict"))
        assert not conflict.success and conflict.result["error_code"] == "file_conflict"
        for name, args in [("file.list", {}), ("file.search", {"query": "changed"})]:
            result = await executor.execute(ToolCall(name, {"path": str(b), **args}, name))
            assert result.success, result.error
            assert "note.txt" in str(result.result)
        facts = await LocalFileOperations().invoke("probe", {"path": str(path)}, executor.context)
        assert facts["sha256"] == edit.result["sha256"]
        assert store.db.execute("SELECT COUNT(*) FROM trusted").fetchone()[0] == 0
        restricted = bound(store, a, scope="workspace")
        denied = await restricted.execute(ToolCall("file.read", {"path": str(path)}, "denied"))
        assert not denied.success
        store.trust(a)
        denied = await restricted.execute(ToolCall("file.read", {"path": str(path)}, "denied2"))
        assert not denied.success and denied.result["error_code"] == "outside_workspace"
    finally:
        await driver.aclose()
        store.close()


def test_scope_defaults_and_invalid_values(tmp_path):
    workspace = WorkspaceSpec((tmp_path / "a",))
    location = ExecutionLocation(tmp_path)
    assert ResourceGrant(workspace, frozenset()).file_access_scope == "workspace"
    with pytest.raises(OperationError, match="explicitly"):
        resolve_resource(workspace, location, "b")
    assert resolve_resource(workspace, location, "b", file_access_scope="user") == (
        Path(os.path.normcase(str(tmp_path / "b"))))
    with pytest.raises(ValueError, match="scope"):
        ResourceGrant(workspace, frozenset(), file_access_scope="invalid")
    with pytest.raises(ValueError, match="current-user"):
        ResourceGrant(workspace, frozenset(), execution_mode="windows_lpac",
                      policy=object(), file_access_scope="user")


async def test_native_draft_cd_and_restore_never_consult_trust(tmp_path, monkeypatch):
    a, b = [tmp_path / name for name in ("A", "B")]
    a.mkdir()
    b.mkdir()
    a, b = canonical_directory(a), canonical_directory(b)
    home = tmp_path / "home"
    def forbidden(*_):
        pytest.fail("native mode requested directory trust")
    monkeypatch.setattr(SQLiteStore, "is_trusted", forbidden)
    controller = SessionController(home, a)
    try:
        controller.configure(cwd=b)
        assert not home.exists(), "editing a draft should not create state"
        session = controller.materialize()
        assert session["granted_roots"] == [str(a)]
        controller.close()
        controller = SessionController(home, a)
        await controller.activate(session["id"])
        assert controller.session["cwd"] == str(b)
        assert controller.store.db.execute("SELECT COUNT(*) FROM trusted").fetchone()[0] == 0
    finally:
        controller.close()
