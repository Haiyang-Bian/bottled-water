"""Installed identity, sanitized diagnostics and read-only legacy replay."""

import hashlib
import json
import os
import subprocess
import sys

from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.config import save_config
from agent_contracts.version import system_version
from test_harness_continuation import engine_for, start
from test_harness_execution import ScriptedModel


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
    assert data["version"] == system_version() and data["database_version"] == 2
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
    path = tmp_path / "state.sqlite3"
    store = SQLiteStore(path)
    engine = engine_for(ScriptedModel(), store)
    result = await (await start(engine)).result()
    await engine.shutdown()
    store.db.execute("DROP TABLE continuation_metadata")
    store.db.execute("PRAGMA user_version=1")
    store.close()
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    replay = cli(tmp_path, "replay", result.run_id)
    assert replay.returncode == 0, replay.stderr
    events = [json.loads(line) for line in replay.stdout.splitlines()]
    assert events[-1]["type"] == "system.run_completed"
    assert before == hashlib.sha256(path.read_bytes()).hexdigest()
    assert not list(tmp_path.glob("*.bak"))
