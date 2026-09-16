"""Opt-in native subset: no administrator, model request, or system ACL changes."""

import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

import pytest


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENTHUB_RUN_LPAC_NATIVE") != "1",
    reason="Requires Windows and explicit AGENTHUB_RUN_LPAC_NATIVE=1",
)
def test_lpac_native_identity_and_lifecycle_subset():
    repo = Path(__file__).resolve().parents[1]
    output = repo / "var" / ("l4a-isolation-test-" + uuid4().hex)
    process = subprocess.run(
        [sys.executable, str(repo / "scripts/probe-lpac-isolation.py"), "--output", str(output)],
        cwd=repo,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert process.returncode == 0, (process.stdout, process.stderr, str(output))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["passed"] is True
    assert set(report["checks"]) == {
        "identity_0",
        "identity_1",
        "distinct_identities",
        "overlap",
        "cancel",
        "timeout",
        "host_crash",
        "network_descendants",
        "startup_assign",
        "startup_token",
        "reject_alias_grants",
        "path_aliases",
        "alias_target_unchanged",
    }
    assert all(value is True for value in report["checks"].values())
    assert all(profile.get("deleted") is True for profile in report["profiles"])
    assert report["cleanup"] == ["A", "B", "R"]
    assert report["p1_release_gate"] == "not_passed"
    # Missing symbolic-link privilege is an explicit coverage gap, never a pass.
    if "symbolic_link" in report.get("not_executed", {}):
        assert "1314" in report["not_executed"]["symbolic_link"]


@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENTHUB_RUN_LPAC_NATIVE") != "1",
    reason="Requires Windows and explicit AGENTHUB_RUN_LPAC_NATIVE=1",
)
def test_lpac_standing_separate_roots_subset():
    """The revised product uses separate roots and leaves ACL inheritance intact."""
    repo = Path(__file__).resolve().parents[1]
    output = repo / "var" / ("l4a-standing-test-" + uuid4().hex)
    process = subprocess.run(
        [sys.executable, str(repo / "scripts/probe-lpac-standing.py"), "--output", str(output)],
        cwd=repo, capture_output=True, text=True, timeout=180, check=False,
    )
    assert process.returncode == 0, (process.stdout, process.stderr, str(output))
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert report["subset_passed"] is True
    assert report["preparation_usable"] is True
    assert all(report["checks"].values())
    assert report["cleanup_verified"] is True
    assert report["acl_mode"] == "separate-roots"
    assert report["inheritance_changes"] == []
    assert report["unrelated_acl_preserved_while_prepared"] is True
    assert report["unrelated_acl_preserved"] is True
    assert report["gate"] == "not_passed"  # Complete lifecycle/toolchain acceptance is separate.
