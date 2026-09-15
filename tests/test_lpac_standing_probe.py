"""Gate evidence validation; no Windows mutation and no simulated security claim."""

import importlib.util
from pathlib import Path

import pytest


source = Path(__file__).resolve().parents[1] / "scripts/lpac_probe/standing.py"
spec = importlib.util.spec_from_file_location("standing_probe", source)
standing = importlib.util.module_from_spec(spec)
spec.loader.exec_module(standing)


def successful_fixture():
    positive = {"read_work", "write_work", "read_archive", "read_own_scratch", "create_work",
                "rename_work_file", "delete_work_file"}
    negative = {"write_archive", "read_private", "write_private", "read_other_scratch",
                "write_dac_archive", "write_owner_archive", "write_dac_work", "write_owner_work",
                "write_dac_anchor", "write_owner_anchor", "delete_child_group", "create_archive",
                "delete_archive_file", "rename_archive", "rename_ancestor", "write_dac_new",
                "write_owner_new"}
    data = {key: {"allowed": True} for key in positive}
    data.update({key: {"allowed": False, "errno": 13, "winerror": 5} for key in negative})
    data["read_work"]["value"] = "work"
    data["read_archive"]["value"] = "archive"
    return data


def test_complete_attempts_required():
    assert all(standing.evaluate(successful_fixture()).values())
    assert not any(standing.evaluate({}).values())


@pytest.mark.parametrize("key", ["read_private", "write_archive", "rename_archive",
                                "rename_ancestor", "write_dac_new"])
def test_actual_bypass_cannot_be_declared_success(key):
    data = successful_fixture()
    data[key] = {"allowed": True}
    assert not standing.evaluate(data)[key]


def test_missing_file_is_not_os_denial():
    data = successful_fixture()
    data["read_private"] = {"allowed": False, "errno": 2, "winerror": 2}
    assert not standing.evaluate(data)["read_private"]


def test_positive_reads_must_reach_the_expected_fixture():
    data = successful_fixture()
    data["read_archive"]["value"] = "different file"
    assert not standing.evaluate(data)["read_archive"]


def test_deny_does_not_cancel_an_excessive_capability_allow():
    acl = {"Archive": {"policy_aces": [
        {"kind": 1, "flags": 3, "mask": standing.CONTENT_WRITE},
        {"kind": 0, "flags": 19, "mask": standing.MODIFY},
    ]}}
    assert "Archive" in standing.audit_allow_masks(acl, {"Archive": standing.READ_EXECUTE})
    assert "Private" in standing.audit_allow_masks({}, {"Private": 0})


def test_inherited_write_must_be_caught_after_read_only_repair():
    acl = {"Archive": {"policy_aces": [
        {"kind": 0, "flags": 3, "mask": standing.READ_EXECUTE},
        {"kind": 0, "flags": 19, "mask": standing.MODIFY & ~0x10000},
    ]}}
    assert standing.audit_allow_masks(acl, {"Archive": standing.READ_EXECUTE})
    acl["Archive"]["policy_aces"].pop()
    assert not standing.audit_allow_masks(acl, {"Archive": standing.READ_EXECUTE})
