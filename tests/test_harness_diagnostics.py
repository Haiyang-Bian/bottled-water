"""Installed identity, sanitized diagnostics and read-only legacy replay."""

import hashlib
import json
import os
import subprocess
import sys

from agent_cli.config import save_config
from agent_contracts.version import system_version
from test_local_environment import legacy


def cli(home, *args):
    env = {**os.environ, "AGENTHUB_HOME": str(home), "DIAGNOSTIC_TEST_KEY": "diagnostic-secret-123"}
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [
            os.environ.get("AGENTHUB_TEST_PYTHON", sys.executable),
            "-B",
            "-m",
            "agent_cli.main",
            *args,
        ],
        env=env,
        cwd=home,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )


def test_version_and_doctor_identify_install_and_hide_secrets(tmp_path):
    save_config(
        tmp_path,
        {
            "default_profile": "test",
            "profiles": {
                "test": {
                    "provider": "openai_compatible",
                    "model": "fixture",
                    "base_url": "http://127.0.0.1",
                    "credential_ref": "env:DIAGNOSTIC_TEST_KEY",
                }
            },
        },
    )
    version = cli(tmp_path, "--version")
    assert version.returncode == 0 and system_version() in version.stdout
    check = cli(tmp_path, "doctor")
    assert check.returncode == 0, check.stderr
    data = json.loads(check.stdout)
    assert data["version"] == system_version() and data["database_version"] is None
    assert not (tmp_path / "state.sqlite3").exists()
    assert data["python_executable"] and not data["filesystem_isolation"]
    assert data["effective_limits"]["execution"]["max_model_turns"] is None
    assert "diagnostic-secret-123" not in check.stdout + check.stderr
    assert "env:DIAGNOSTIC_TEST_KEY" not in check.stdout


def test_doctor_reports_partial_capabilities_without_configuration(tmp_path):
    check = cli(tmp_path, "doctor")
    assert check.returncode == 2
    data = json.loads(check.stdout)
    assert data["errors"] and data["version"] and data["python_executable"]


async def test_replay_v1_does_not_migrate_or_rewrite_old_events(tmp_path):
    import sqlite3
    from dataclasses import asdict
    from agent_runtime.core.run_types import EventEnvelope
    path = tmp_path / "state.sqlite3"
    legacy(path, 1)
    event = EventEnvelope("r", "old", 1, "system.run_completed", {})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO events VALUES(?,?,?,?)", (
            event.event_id, "r", 1, json.dumps(asdict(event), default=str),
        ))
        db.execute("UPDATE runs SET sequence=1,state='completed',result='{}' WHERE id='r'")
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    replay = cli(tmp_path, "replay", "r")
    assert replay.returncode == 0, replay.stderr
    events = [json.loads(line) for line in replay.stdout.splitlines()]
    assert events[-1]["type"] == "system.run_completed"
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not list(tmp_path.glob("*.bak"))
