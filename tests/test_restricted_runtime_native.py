"""Opt-in P2 acceptance; no elevation, model requests or system ACL changes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


@pytest.mark.skipif(sys.platform != "win32" or os.environ.get("AGENTHUB_RUN_LPAC_NATIVE") != "1",
                   reason="Requires Windows and explicit AGENTHUB_RUN_LPAC_NATIVE=1")
@pytest.mark.parametrize("script,prefix", [("probe-restricted-runtime.py", "l4a-runtime-native"),
                                          ("probe-restricted-failures.py", "l4a-runtime-faults-native")])
def test_restricted_runtime_native(script, prefix):
    repo = Path(__file__).resolve().parents[1]
    output = repo / "var" / (prefix + "-" + uuid4().hex)
    process = subprocess.run([sys.executable, str(repo / "scripts" / script), "--output", str(output)],
        cwd=repo, capture_output=True, text=True, timeout=180)
    assert process.returncode == 0, (process.stdout, process.stderr, str(output))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True and report["cleanup_verified"] is True
    if "cases" in report:
        assert len(report["cases"]) == 7
        assert all(all(case["checks"].values()) for case in report["cases"].values())
    else:
        assert not report["host_bypasses"]
        assert report["not_executed"] == ["full_toolchain_requires_initialization"]
