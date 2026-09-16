"""Stable local serialization. No OS identities in public policy snapshots."""

from dataclasses import asdict
from pathlib import Path

from agent_contracts.permissions import (
    ExecutionPolicySnapshot, PathPermission, StandingPermissionPolicy, TaskPermissionSelection,
)


def rules(values):
    return tuple(PathPermission(Path(row["path"]), row["access"]) for row in values)


def policy_from_dict(value):
    return StandingPermissionPolicy(**{
        **value, **{key: rules(value.get(key, [])) for key in ("grants", "protections", "mandatory")},
    })


def selection_from_dict(value):
    return TaskPermissionSelection(value.get("mode", "inherit"), rules(value.get("roots", [])))


def snapshot_from_dict(value):
    return ExecutionPolicySnapshot(policy_from_dict(value["policy"]),
                                   selection_from_dict(value["selection"]), value["digest"],
                                   value.get("network", "deny"))


def document(value):
    import json
    return json.dumps(asdict(value), ensure_ascii=False, sort_keys=True, default=str)
