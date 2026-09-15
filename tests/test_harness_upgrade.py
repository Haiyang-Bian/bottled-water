"""Upgrade old wheel state, including user-scoped DPAPI credentials."""

import hashlib
import json
import os
import subprocess
import sqlite3
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
    with sqlite3.connect(home / "state.sqlite3") as old_db:
        original_environment = (
            old_db.execute("SELECT environment_id FROM local_environment").fetchone()[0]
            if identity["schema"] >= 3
            else None
        )
    assert identity["schema"] == int(os.environ["AGENTHUB_LEGACY_SCHEMA"])
    config_before = hashlib.sha256((home / "config.toml").read_bytes()).hexdigest()
    credentials_before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in (home / "credentials").iterdir()
        if p.is_file()
    }
    doctor = subprocess.run(
        [new_python, "-B", "-m", "agent_cli.main", "doctor"],
        env=env,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert doctor.returncode == 0, doctor.stderr
    assert json.loads(doctor.stdout)["database_version"] == identity["schema"]
    assert json.loads(doctor.stdout)["upgrade_required"]
    assert not list(home.glob("*.bak"))
    upgraded = subprocess.run(
        [new_python, "-B", "-m", "agent_cli.main", "state", "upgrade"],
        env=env,
        cwd=project,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    assert json.loads(upgraded.stdout)["database_version"] == 5
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
        assert store.environment.default_agent_id == "local"
        if original_environment:
            assert store.environment.environment_id == original_environment
        row = store.queries().catalog()[0]
        assert row["id"] == identity["session_id"] and row["cwd"] == str(project)
        import asyncio

        snapshot = asyncio.run(store.load(identity["session_id"]))
        assert snapshot.version == 1 and snapshot.messages[-1]["content"] == "Legacy answer"
        if identity.get("memory_ids"):
            from agent_adapters.storage.memory import SQLiteMemory
            memory = SQLiteMemory(store)
            ids = {r.id for r in memory.search(memory.access())}
            assert identity["memory_ids"]["active"] in ids
            assert identity["memory_ids"]["forgotten"] not in ids
            memory.rebuild(memory.access())
            assert identity["memory_ids"]["forgotten"] not in {r.id for r in memory.search(memory.access())}
        assert not store.db.execute("SELECT 1 FROM resource_jobs").fetchone()
    finally:
        store.close()
    assert list(home.glob("*.bak"))
    with sqlite3.connect(next(home.glob("*.bak"))) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == identity["schema"]
    # Old binaries refuse v5 instead of silently overwriting it.
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
