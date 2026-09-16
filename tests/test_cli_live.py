"""Opt-in external provider acceptance; never substitute fixture results for live results."""

import os
import subprocess
import sys

import pytest

from agent_cli.config import load_config, select_profile
from agent_adapters.credentials.local import LocalCredentialStore
from pathlib import Path

pytestmark = pytest.mark.live


@pytest.mark.parametrize(
    "provider,variable",
    [
        ("openai_compatible", "AGENTHUB_LIVE_OPENAI_PROFILE"),
        ("deepseek", "AGENTHUB_LIVE_DEEPSEEK_PROFILE"),
    ],
)
def test_live_provider_repairs_a_local_project(tmp_path, provider, variable):
    profile_name = os.environ.get(variable)
    source_home = os.environ.get("AGENTHUB_LIVE_HOME")
    if not source_home or not profile_name:
        pytest.skip(f"Explicit {variable} and AGENTHUB_LIVE_HOME are required")
    _, profile = select_profile(load_config(Path(source_home)), profile_name)
    assert profile.provider == provider
    secret = LocalCredentialStore(Path(source_home) / "credentials").resolve(profile.credential_ref)
    project = tmp_path / "project"
    project.mkdir()
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    environment = {
        **os.environ,
        "AGENTHUB_HOME": str(tmp_path / "config"),
        "AGENTHUB_LIVE_TEST_KEY": secret,
    }
    environment.pop("PYTHONPATH", None)
    python = os.environ.get("AGENTHUB_TEST_PYTHON", sys.executable)

    def run(*args):
        result = subprocess.run(
            [python, "-B", "-m", "agent_cli.main", *args],
            cwd=project,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=300,
        )
        assert secret not in result.stdout and secret not in result.stderr
        assert result.returncode == 0, (
            f"CLI failed ({result.returncode}); inspect redacted local logs"
        )
        return result

    run(
        "init",
        "--provider",
        provider,
        "--model",
        profile.model,
        "--base-url",
        profile.base_url,
        "--credential-env",
        "AGENTHUB_LIVE_TEST_KEY",
    )
    run(
        "--json",
        "-p",
        "Read calc.py, fix add(a,b) to return their sum, and run a Python assertion "
        f"that add(2,3)==5 using powershell.run and this Python executable: {sys.executable}. "
        "Only modify calc.py. Report actual test results. Do not access network or change global settings.",
    )
    check = subprocess.run(
        [sys.executable, "-c", "from calc import add; assert add(2,3)==5"],
        cwd=project,
        capture_output=True,
        timeout=20,
    )
    assert check.returncode == 0
    run(
        "--continue",
        "--json",
        "-p",
        "Which file did you change in the previous turn? Answer briefly.",
    )
