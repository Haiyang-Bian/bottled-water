"""The feasibility checker must never confuse a failed launcher with OS isolation."""

import importlib.util
import json
import os
from pathlib import Path
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1] / "scripts/lpac_probe/validation.py"
spec = importlib.util.spec_from_file_location("lpac_validation", SOURCE)
validation = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validation)

namespace_spec = importlib.util.spec_from_file_location(
    "lpac_namespace", SOURCE.with_name("namespace.py")
)
namespace = importlib.util.module_from_spec(namespace_spec)
sys.modules[namespace_spec.name] = namespace
namespace_spec.loader.exec_module(namespace)


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


@pytest.mark.parametrize("value", ["", "..", "a" * 31, "A" * 32, "internetClient", "0/" * 16])
def test_namespace_capability_cannot_select_an_arbitrary_identity(value):
    with pytest.raises(ValueError):
        namespace.capability_name(value)


def test_namespace_manifest_only_grants_fixed_read_query_rights():
    assert namespace.capability_name("0" * 32) == "AgentHub.Probe.Namespace." + "0" * 32
    assert [(target.kind, target.mask) for target in namespace.TARGETS] == [
        ("Directory", 0x20003),
        ("SymbolicLink", 0x20001),
        ("SymbolicLink", 0x20001),
        ("SymbolicLink", 0x20001),
        ("Device", 0x120089),
    ]
    assert [target.path for target in namespace.TARGETS] == [
        "\\GLOBAL??",
        "\\GLOBAL??\\C:",
        "\\GLOBAL??\\D:",
        "\\GLOBAL??\\MountPointManager",
        "\\\\.\\MountPointManager",
    ]
    assert all(not target.mask & 0xD0000 for target in namespace.TARGETS)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ACL representation")
def test_namespace_cleanup_preserves_concurrent_unrelated_entries():
    import win32security as security

    owned_sid = "S-1-5-21-100-200-300-400"
    unrelated = security.ConvertStringSidToSid("S-1-5-21-100-200-300-401")
    acl = security.ACL()
    acl.AddAccessAllowedAceEx(2, 0, 0x20003, security.ConvertStringSidToSid(owned_sid))
    acl.AddAccessAllowedAceEx(2, 0, 0x123, unrelated)
    expected = acl.GetAce(1)
    assert namespace.remove_owned_ace(acl, owned_sid, 0x20003)
    assert acl.GetAceCount() == 1
    assert acl.GetAce(0) == expected
    assert not namespace.remove_owned_ace(acl, owned_sid, 0x20003)


@pytest.mark.skipif(os.name != "nt", reason="Real Windows ACL representation")
@pytest.mark.parametrize("mask,flags,count", [(0x20001, 0, 1), (0x20003, 3, 1), (0x20003, 0, 2)])
def test_namespace_cleanup_refuses_modified_or_ambiguous_managed_ace(mask, flags, count):
    import win32security as security

    owned_sid = "S-1-5-21-100-200-300-400"
    acl = security.ACL()
    for _ in range(count):
        acl.AddAccessAllowedAceEx(2, flags, mask, security.ConvertStringSidToSid(owned_sid))
    with pytest.raises(RuntimeError, match="manual inspection"):
        namespace.remove_owned_ace(acl, owned_sid, 0x20003)
    assert acl.GetAceCount() == count
