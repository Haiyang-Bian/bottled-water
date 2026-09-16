"""Opt-in formal CLI acceptance against explicitly initialized isolated tool copies."""

import json
import os
from pathlib import Path
import sqlite3

import pytest

from test_cli_integration import cli_fixture as cli_fixture, records

pytestmark = pytest.mark.skipif(
    os.name != "nt" or not os.environ.get("AGENTHUB_P3_SETUP_HOME"),
    reason="Explicit isolated AGENTHUB_P3_SETUP_HOME with formal initialization required",
)


def test_formal_cli_inherit_tools_os_denials_and_restart(cli_fixture):
    run, project, archive, requests = cli_fixture
    source = Path(os.environ["AGENTHUB_P3_SETUP_HOME"])
    from agent_cli.sandbox import require_setup
    value, _ = require_setup(source)
    home = Path(run.environment["AGENTHUB_HOME"])
    (home / "sandbox" / "runs").mkdir(parents=True)
    (home / "sandbox" / "setup.json").write_text(json.dumps(value), encoding="utf-8")
    private = project.parent / "Private"
    private.mkdir()
    (private / "secret.txt").write_text("not authorized", encoding="utf-8")
    run("--json", "permissions", "setup", "--revision", "0",
        "--write-dir", str(project), "--read-dir", str(archive))
    answer = records(run("--json", "-p", "RESTRICTED"))
    assert answer[-1]["state"] == "completed"
    assert "a + b" in (project / "calc.py").read_text(encoding="utf-8")
    results = [event["payload"] for event in answer if event.get("type") == "agent.tool_result"]
    assert len(results) == 7
    assert all(value["success"] for value in results[:5])
    assert results[5]["success"] is False
    native = json.dumps(results[6])
    assert results[6]["success"] and "OS DENIED" in native and "NETWORK DENIED" in native
    assert (archive / "note.txt").read_text(encoding="utf-8") == "separate project"
    assert (private / "secret.txt").read_text(encoding="utf-8") == "not authorized"
    run("--json", "-c", "-p", "REMEMBER", cwd=archive)
    assert sum(message["role"] == "user" and message["content"] == "RESTRICTED"
               for message in requests[-1]["messages"]) == 1
    with sqlite3.connect(home / "state.sqlite3") as db:
        assert db.execute("SELECT mode FROM task_permissions").fetchone()[0] == "windows_lpac"
        assert db.execute("SELECT count(*) FROM permission_preparations WHERE state!='retired'").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM permission_leases WHERE state!='closed'").fetchone()[0] == 0
