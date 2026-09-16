"""Run remaining native gates without hiding missing system initialization."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENTHUB_RUN_LPAC_NATIVE") != "1",
    reason="Requires explicit ordinary-user native acceptance",
)
def test_remaining_gates_and_missing_initialization():
    repo = Path(__file__).resolve().parents[1]
    output = repo / "var" / ("l4a-completion-test-" + uuid4().hex)
    result = subprocess.run(
        [sys.executable, str(repo / "scripts/probe-lpac-completion.py"), "--output", str(output)],
        cwd=repo, capture_output=True, text=True, timeout=240, check=False,
    )
    report = json.loads((output / "report.json").read_text())
    assert result.returncode == 1 and report["gate"] == "not_passed", result.stderr
    assert not report.get("error"), report
    assert all(report["checks"][key] is True for key in (
        "policies", "isolation", "objects", "desktop_job_inherited", "run_jobs_closed"))
    assert report["checks"]["toolchain"] is False
    assert report["not_executed"] == ["fixed_namespace_toolchain"]
    policy = json.loads(Path(report["reports"]["policies"]["path"]).read_text())
    assert len(policy["extra_checks"]) == 12 and all(policy["extra_checks"].values())
    assert policy["cleanup_verified"] is True
    assert all(record["deleted"] for record in policy["profiles"])
