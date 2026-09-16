"""A successful probe must not hide incomplete cleanup or skipped native coverage."""

from copy import deepcopy
import importlib.util
from pathlib import Path

import pytest

path = Path(__file__).resolve().parents[1] / "scripts/lpac_probe/gate_validation.py"
spec = importlib.util.spec_from_file_location("gate_validation", path)
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


@pytest.mark.parametrize("change", ["missing", "false", "truthy", "skip", "coverage", "cleanup",
                                    "target", "fixture", "error", "identity"])
def test_incomplete_native_gate_is_not_passed(change):
    report = {"checks": {key: True for key in gate.REQUIRED}, "namespace_experiment": "a" * 32}
    initialization = {"status": "cleaned", "cleanup_errors": [],
                      "targets": [{"state": "removed"} for _ in range(5)],
                      "symbolic_fixture": {"created": True}, "experiment": "a" * 32}
    assert gate.assess(report, initialization)["gate"] == "passed"
    if change == "missing":
        report["checks"].pop("symbolic_link")
    elif change in {"false", "truthy"}:
        report["checks"]["symbolic_link"] = False if change == "false" else 1
    elif change in {"skip", "coverage"}:
        report["not_executed" if change == "skip" else "coverage_limits"] = ["symbolic_link"]
    elif change == "cleanup":
        initialization["cleanup_errors"] = ["failed removal"]
    elif change == "target":
        initialization["targets"][0]["state"] = "applied"
    elif change == "fixture":
        initialization.pop("symbolic_fixture")
    elif change == "identity":
        initialization["experiment"] = "b" * 32
    else:
        report["error"] = "probe crashed"
    snapshot = deepcopy((report, initialization))
    assert gate.assess(report, initialization)["gate"] == "not_passed"
    assert (report, initialization) == snapshot
