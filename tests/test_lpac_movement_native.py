"""Opt-in security gate, intentionally failing while stale policy reuse is unsafe.

This is not xfail or a test that treats a known bypass as successful isolation.
The ordinary unit tests check evidence handling without touching Windows ACLs.
"""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENTHUB_RUN_LPAC_MOVEMENT_GATE") != "1",
    reason="Requires Windows and explicit AGENTHUB_RUN_LPAC_MOVEMENT_GATE=1",
)
def test_movement_and_generation_reuse_native_gate():
    repo = Path(__file__).resolve().parents[1]
    output = repo / "var" / ("l4a-movement-test-" + uuid4().hex)
    process = subprocess.run(
        [sys.executable, str(repo / "scripts/probe-lpac-movement.py"), "--output", str(output)],
        cwd=repo, capture_output=True, text=True, timeout=180, check=False,
    )
    report_path = output / "report.json"
    assert report_path.is_file(), (process.stdout, process.stderr)
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("error") is None, report.get("error")
    assert report.get("cleanup_verified") is True, report.get("cleanup_error")
    assert all(record.get("deleted") is True for record in report["profiles"])
    assert all(result.get("job_drained") is True for result in report["results"].values())
    assert process.returncode == 0 and report["subset_passed"] is True, {
        "report": str(report_path), "stop_reason": report["stop_reason"],
        "bypasses": report["bypasses"], "checks": report["checks"],
    }
