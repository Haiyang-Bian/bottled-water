"""Negative evidence must stop the native gate; a fresh control cannot erase it."""

import importlib.util
import json
from pathlib import Path

import pytest


source = Path(__file__).resolve().parents[1] / "scripts/lpac_probe/movement.py"
spec = importlib.util.spec_from_file_location("movement_probe", source)
movement = importlib.util.module_from_spec(spec)
spec.loader.exec_module(movement)


def outcome():
    data = {"read_work": {"allowed": True, "value": "work"},
            "write_work": {"allowed": True, "value": 7},
            "read_archive": {"allowed": True, "value": "archive"},
            "read_moved_archive": {"allowed": True, "value": "archive-move"},
            "read_copied_archive": {"allowed": True, "value": "copy"}}
    for name in ("write_archive", "read_private", "write_private", "write_moved_archive",
                 "write_copied_archive", "read_moved_private", "write_moved_private",
                 "read_copied_private", "write_copied_private", "read_detached_root",
                 "write_detached_root", "read_moved_directory", "write_moved_directory"):
        data[name] = {"allowed": False, "winerror": 5, "errno": 13}
    return {"exit_code": 0, "timed_out": False, "cancelled": False, "truncated": False,
            "job_drained": True, "pipe_errors": [], "stdout": json.dumps(data),
            "token": {"appcontainer": True, "lpac": True, "sid_matches": True,
                      "capabilities": ["policy"]}}


def test_movement_requires_verified_identity_positive_reads_and_os_denials():
    result = outcome()
    checks = movement.evaluate(result, "policy", moved=True)
    assert all(checks.values())
    assert not movement.bypasses(result, checks)
    assert not all(movement.evaluate({}, "policy", moved=True).values())


@pytest.mark.parametrize("key", ["write_moved_archive", "read_moved_private",
                                "read_moved_directory", "read_detached_root"])
def test_actual_access_is_retained_even_when_fresh_generation_passes(key):
    stale, fresh = outcome(), outcome()
    data = json.loads(stale["stdout"])
    data[key] = {"allowed": True, "value": "fixture secret"}
    stale["stdout"] = json.dumps(data)
    stale_checks = movement.evaluate(stale, "policy", moved=True)
    assert not stale_checks[key]
    assert movement.bypasses(stale, stale_checks) == [key]
    assert all(movement.evaluate(fresh, "policy", moved=True).values())
    assert not all(stale_checks.values())


@pytest.mark.parametrize("value", [{"allowed": False, "winerror": 2, "errno": 2},
                                  {"allowed": False}, None, "denied"])
def test_missing_file_or_malformed_result_is_not_permission_denial(value):
    result = outcome()
    data = json.loads(result["stdout"])
    data["read_moved_private"] = value
    result["stdout"] = json.dumps(data)
    checks = movement.evaluate(result, "policy", moved=True)
    assert not checks["read_moved_private"]
    assert not movement.bypasses(result, checks)


@pytest.mark.parametrize("change", [{"exit_code": 5}, {"job_drained": False},
                                    {"cancelled": True}, {"timed_out": True},
                                    {"truncated": True}, {"pipe_errors": ["broken"]},
                                    {"token": {}}, {"token": None},
                                    {"stdout": "[]"}, {"stdout": "not json"}])
def test_failed_or_incomplete_launch_cannot_count_as_native_success(change):
    result = {**outcome(), **change}
    checks = movement.evaluate(result, "policy", moved=True)
    assert not all(checks.values())
    assert not movement.bypasses(result, checks)


def test_wrong_file_contents_cannot_satisfy_positive_control():
    result = outcome()
    data = json.loads(result["stdout"])
    data["read_work"]["value"] = "unrelated"
    result["stdout"] = json.dumps(data)
    assert not movement.evaluate(result, "policy", moved=True)["read_work"]


def report():
    phases = ("baseline", "reused_after_children_moved", "reused_after_root_replaced",
              "fresh_generation")
    return {"checks": {name: movement.evaluate(outcome(), "policy", moved=name != "baseline")
                       for name in phases},
            "bypasses": {name: [] for name in phases},
            "cleanup_verified": True, "unrelated_acl_preserved": True}


@pytest.mark.parametrize("mutation", ["failure", "omitted_phase", "empty_phase", "bypass",
                                     "cleanup", "unrelated_acl", "omitted_bypasses"])
def test_aggregate_cannot_hide_stale_generation_or_incomplete_cleanup(mutation):
    data = report()
    assert movement.subset_passed(data)
    if mutation == "failure":
        data["checks"]["reused_after_children_moved"]["read_moved_private"] = False
    elif mutation == "omitted_phase":
        del data["checks"]["reused_after_children_moved"]
    elif mutation == "empty_phase":
        data["checks"]["reused_after_children_moved"] = {}
    elif mutation == "bypass":
        data["bypasses"]["reused_after_children_moved"] = ["read_moved_private"]
    elif mutation == "cleanup":
        data["cleanup_verified"] = False
    elif mutation == "unrelated_acl":
        data["unrelated_acl_preserved"] = False
    else:
        del data["bypasses"]["reused_after_children_moved"]
    assert not movement.subset_passed(data)
