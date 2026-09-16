"""Native executable selection, literal argv, networking and observed outputs."""

import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from agent_adapters.local.processes import LocalProcessDriver, native_executable
from agent_adapters.local.resources import discover
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import OperationError
from agent_runtime.core.types import ToolCall
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory
from agent_subsystems.workspaces.resources import observations
from test_native_access import bound


async def test_literal_argv_network_and_external_cache_without_registration(tmp_path, monkeypatch):
    a, b, cache = [tmp_path / name for name in ("A", "中文 B", "cache outside")]
    for path in (a, b, cache):
        path.mkdir()
    a, b = canonical_directory(a), canonical_directory(b)
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"network works")

        def log_message(self, *_):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setenv("AGENTHUB_TEST_API_KEY", "secret-native-test")
    store = SQLiteStore(tmp_path / "home/state.sqlite3")
    driver = LocalProcessDriver(Redactor(["secret-native-test"]))
    target = cache / "result.txt"
    literal = "中文 with spaces ; $(bad) & | > < \" quote"
    code = (
        "import os,sys,json,urllib.request; from pathlib import Path; "
        "assert 'AGENTHUB_TEST_API_KEY' not in os.environ; "
        "data=urllib.request.urlopen(sys.argv[2],timeout=3).read(); "
        "Path(sys.argv[3]).write_bytes(data); print(json.dumps([os.getcwd(),sys.argv[1]]))"
    )
    try:
        executor = bound(store, a, driver)
        result = await executor.execute(ToolCall("process.run", {
            "executable": sys.executable, "args": [
                "-c", code, literal, f"http://127.0.0.1:{server.server_port}/", str(target)],
            "cwd": str(b), "outputs": [str(target)],
        }, "native-call"))
        assert result.success, result.error
        actual_cwd, actual_arg = json.loads(result.result["stdout"])
        assert canonical_directory(actual_cwd) == b and actual_arg == literal
        assert result.result["execution"]["executable"] == str(Path(sys.executable).resolve())
        assert result.result["elapsed_seconds"] >= 0
        output = result.result["outputs"][0]
        assert not output["before"]["exists"] and output["after"]["sha256"]
        assert target.read_text() == "network works"
        observed = observations({"type": "agent.tool_result", "payload": {
            "tool": "process.run", "success": True, "result": result.result}})
        assert observed[0][0] == os.path.normcase(str(target))
        assert "secret-native-test" not in str(result.result)
        assert store.db.execute("SELECT COUNT(*) FROM trusted").fetchone()[0] == 0
    finally:
        await driver.aclose()
        store.close()
        await asyncio.to_thread(server.shutdown)
        server.server_close()
        thread.join(2)


def test_executable_selection_rejects_alias_and_implicit_batch(tmp_path, monkeypatch):
    from agent_adapters.local import processes
    monkeypatch.setattr(processes.shutil, "which", lambda _: None)
    with pytest.raises(OperationError, match="PATH"):
        native_executable("unknown", tmp_path)
    with pytest.raises(OperationError, match="WindowsApps"):
        native_executable(str(tmp_path / "Microsoft/WindowsApps/python.exe"), tmp_path)
    script = tmp_path / "script.cmd"
    script.write_text("echo no implicit shell")
    with pytest.raises(OperationError, match="batch"):
        native_executable(str(script), tmp_path)
    if os.name == "nt":
        with pytest.raises(OperationError, match="Drive-relative"):
            native_executable("C:python.exe", tmp_path)
    assert native_executable(sys.executable, tmp_path) == str(Path(sys.executable).resolve())


async def test_discovery_is_read_only_with_sources_and_no_native_tools_for_bounded_scope(tmp_path):
    python = tmp_path / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_text("discovery must not execute this fixture")
    rows = discover("python", tmp_path)
    assert rows[0]["source"] == "project_venv"
    assert any(r["source"] == "agenthub_interpreter" for r in rows)
    store = SQLiteStore(tmp_path / "home/state.sqlite3")
    try:
        executor = bound(store, canonical_directory(tmp_path))
        value = await executor.execute(ToolCall("software.discover", {"kind": "python"}, "find"))
        assert value.success and not value.result["registered"]
        other = bound(store, canonical_directory(tmp_path), scope="workspace")
        names = {t["function"]["name"] for t in await other.list_tools()}
        assert "process.run" not in names and "software.discover" not in names
    finally:
        store.close()


async def test_native_command_timeout_preserves_tool_failure(tmp_path):
    store = SQLiteStore(tmp_path / "home/state.sqlite3")
    driver = LocalProcessDriver(Redactor())
    try:
        executor = bound(store, canonical_directory(tmp_path), driver)
        result = await executor.execute(ToolCall("process.run", {
            "executable": sys.executable, "args": ["-c", "import time; time.sleep(30)"],
            "timeout": 0.2,
        }, "timeout"))
        assert not result.success and result.result["error_code"] == "process_timeout"
        assert not driver.jobs and not driver.processes
    finally:
        await driver.aclose()
        store.close()
