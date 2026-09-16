"""Opt-in native acceptance for the user-selected explicit, idle-only transition."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENTHUB_RUN_LPAC_NATIVE") != "1",
    reason="Requires ordinary Windows user and explicit AGENTHUB_RUN_LPAC_NATIVE=1",
)
def test_quiescent_withdrawal_and_readonly_transition():
    repo = Path(__file__).resolve().parents[1]
    output = repo / "var" / ("l4a-quiescent-test-" + uuid4().hex)
    process = subprocess.run(
        [sys.executable, str(repo / "scripts/probe-lpac-quiescent.py"), "--output", str(output)],
        cwd=repo, capture_output=True, text=True, timeout=180, check=False,
    )
    assert process.returncode == 0, (process.stdout, process.stderr, str(output))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["subset_passed"] is True
    assert set(report["checks"]) == {
        "baseline", "overlap", "busy_refused", "cancelled_and_drained", "old_retired",
        "late_launch_refused", "retired_native_denied", "new_readonly_policy", "fresh_generation",
        "old_capability_absent", "prepare_rollback", "repair_required", "repair_blocks_launch",
        "repair_verified",
    }
    assert all(report["checks"].values())
    assert report["cleanup_verified"] is True
    assert all(item.get("deleted") is True for item in report["profiles"])
    assert all(item.get("cleanup_verified") is True for item in report["preparations"])
    assert report["gate"] == "not_passed"  # The complete toolchain/security gate is still separate.
