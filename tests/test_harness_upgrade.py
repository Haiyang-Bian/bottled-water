"""Upgrade real 0.1.0 wheel state, including user-scoped DPAPI credentials."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from agent_adapters.storage.sqlite import SQLiteStore


def test_installed_upgrade_preserves_legacy_identity_and_history(tmp_path):
    old_python = os.environ.get("AGENTHUB_LEGACY_PYTHON")
    new_python = os.environ.get("AGENTHUB_TEST_PYTHON")
    if not old_python or not new_python:
        pytest.skip("Explicit independent old/new wheel installations are required")
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    env = {**os.environ, "AGENTHUB_HOME": str(home)}
    env.pop("PYTHONPATH", None)
    seed = subprocess.run(
        [
            old_python,
            "-B",
            str(Path(__file__).parent / "helpers" / "seed_legacy_state.py"),
            str(home),
            str(project),
        ],
        env=env,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert seed.returncode == 0, seed.stderr
    identity = json.loads(seed.stdout)
    assert identity["schema"] == 1
    config_before = hashlib.sha256((home / "config.toml").read_bytes()).hexdigest()
    credentials_before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (home / "credentials").iterdir()
        if p.is_file()
    }
    upgraded = subprocess.run(
        [new_python, "-B", "-m", "agent_cli.main", "doctor"],
        env=env,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    assert json.loads(upgraded.stdout)["database_version"] == 2
    assert "upgrade-test-private-key" not in upgraded.stdout + upgraded.stderr
    assert config_before == hashlib.sha256((home / "config.toml").read_bytes()).hexdigest()
    assert credentials_before == {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (home / "credentials").iterdir()
        if p.is_file()
    }
    store = SQLiteStore(home / "state.sqlite3")
    try:
        assert store.session(identity["session_id"]) and store.is_trusted(project)
        import asyncio

        snapshot = asyncio.run(store.load(identity["session_id"]))
        assert snapshot.version == 1 and snapshot.messages[-1]["content"] == "Legacy answer"
    finally:
        store.close()
    assert list(home.glob("*.bak"))
    # A 0.1.0 binary refuses v2 instead of silently overwriting it.
    old = subprocess.run(
        [old_python, "-B", "-m", "agent_cli.main", "sessions"],
        env=env,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert old.returncode == 2 and "version" in old.stderr.lower()
