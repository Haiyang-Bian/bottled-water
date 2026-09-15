"""The feasibility checker must never confuse a failed launcher with OS isolation."""

import importlib.util
import json
from pathlib import Path

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/lpac_probe/validation.py"
spec = importlib.util.spec_from_file_location("lpac_validation", SOURCE)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)


def result(name, code=0, stdout="", stderr=""):
    return {
        "name": name,
        "exit_code": code,
        "stdout": stdout,
        "stderr": stderr,
        "timed_out": False,
        "truncated": False,
        "job_drained": True,
        "token": {"appcontainer": True, "lpac": True, "sid_matches": True},
    }


def basic():
    return [
        result("read_A", stdout="A-fixture"),
        result("write_A", 1),
        result("write_B"),
        result("read_C", 1),
    ]


def matrix():
    data = {"read_A": {"allowed": True, "value": "A-fixture"}, "write_B": {"allowed": True}}
    data.update(
        {
            name: {"allowed": False, "errno": 13}
            for name in ("write_A", "read_C", "read_host_state", "write_runtime")
        }
    )
    return data


def test_missing_evidence_is_not_a_pass():
    assert not all(validation.evaluate([], full=True).values())


@pytest.mark.parametrize(
    "change",
    [
        {"timed_out": True},
        {"exit_code": 5},
        {"stdout": ""},
        {"job_drained": False},
        {"token": {"appcontainer": True}},
        {"truncated": True},
    ],
)
def test_positive_access_requires_working_launcher_and_verified_token(change):
    reports = basic()
    reports[0].update(change)
    assert not all(validation.evaluate(reports).values())


def test_basic_smoke_is_not_toolchain_acceptance():
    assert all(validation.evaluate(basic()).values())
    assert not all(validation.evaluate(basic(), full=True).values())


def test_git_cwd_failure_cannot_count_as_working_permission_boundary():
    reports = basic()
    reports += [
        result(name, 128, stderr="Unable to read current working directory: Permission denied")
        for name in ("git_read_A", "git_write_A", "git_write_B", "git_read_C")
    ]
    checks = validation.evaluate(reports, full=True)
    assert not checks["git_read_A"]
    assert not checks["git_write_B"]
    assert not checks["git_write_A_denied"]
    assert not checks["git_read_C_denied"]
    assert not all(checks.values())


@pytest.mark.parametrize("name", ["write_A", "read_C", "read_host_state", "write_runtime"])
def test_negative_check_requires_permission_denial_not_missing_path(name):
    data = matrix()
    assert validation._matrix(json.dumps(data), child=False)
    data[name]["errno"] = 2
    assert not validation._matrix(json.dumps(data), child=False)


def test_child_result_is_required_and_checked():
    data = matrix()
    assert not validation._matrix(json.dumps(data))
    child = matrix()
    data["child"] = {"exit_code": 0, "stdout": json.dumps(child)}
    assert validation._matrix(json.dumps(data))
    child["read_C"] = {"allowed": True, "value": "C-fixture"}
    data["child"]["stdout"] = json.dumps(child)
    assert not validation._matrix(json.dumps(data))


def test_network_timeout_is_not_os_denial():
    network = {
        name: {"allowed": False, "winerror": 10013}
        for name in ("ipv4_tcp", "ipv4_udp", "ipv6_tcp", "ipv6_udp")
    }
    reports = basic() + [result("python_network", stdout=json.dumps(network))]
    assert validation.evaluate(reports, full=True)["network_denied"]
    network["ipv4_tcp"]["winerror"] = 10060
    reports[-1]["stdout"] = json.dumps(network)
    assert not validation.evaluate(reports, full=True)["network_denied"]


def test_duplicate_results_fail():
    assert not validation.evaluate(basic() + [result("read_A", stdout="A-fixture")])[
        "unique_results"
    ]
