"""Pure authority calculation, separate from ACL preparation and filesystem I/O.

All inputs are canonical paths supplied by a trusted adapter. The native driver
must independently enforce the result, including aliases and object replacement.
"""

from dataclasses import asdict
import hashlib
import json

from agent_contracts.permissions import (
    ExecutionPolicySnapshot,
    PathAccess,
    PathPermission,
    PermissionDecision,
    StandingPermissionPolicy,
    TaskPermissionSelection,
)

_RANK = {PathAccess.DENY: 0, PathAccess.READ: 1, PathAccess.MODIFY: 2}
_OPERATIONS = {
    "read": PathAccess.READ, "list": PathAccess.READ, "execute": PathAccess.READ,
    "create": PathAccess.MODIFY, "modify": PathAccess.MODIFY,
    "rename": PathAccess.MODIFY, "delete": PathAccess.MODIFY,
}


def _path(path):
    return PathPermission(path, PathAccess.READ).path


def _grant_at(rules, path):
    return max(
        (rule.access for rule in rules if path.is_relative_to(rule.path)),
        key=_RANK.__getitem__, default=PathAccess.DENY,
    )


def _policy_access(policy, path):
    if not policy.enabled:
        return PathAccess.DENY, "policy_disabled"
    access = _grant_at(policy.grants, path)
    reason = "granted" if access != PathAccess.DENY else "outside_grants"
    for rules, source in ((policy.protections, "protected_path"),
                          (policy.mandatory, "mandatory_protection")):
        for rule in rules:
            if path.is_relative_to(rule.path) and _RANK[rule.access] <= _RANK[access]:
                access, reason = rule.access, source
    return access, reason


def _effective(policy, selection, path):
    access, reason = _policy_access(policy, path)
    if selection.mode == "custom":
        selected = _grant_at(selection.roots, path)
        if _RANK[selected] < _RANK[access]:
            access, reason = selected, "task_restriction"
    return access, reason


def freeze_policy(policy: StandingPermissionPolicy,
                  selection: TaskPermissionSelection | None = None) -> ExecutionPolicySnapshot:
    selection = selection or TaskPermissionSelection()
    if not policy.enabled:
        raise ValueError("Long-term policy is not enabled")
    for rule in selection.roots:
        access, _ = _policy_access(policy, rule.path)
        if _RANK[rule.access] > _RANK[access]:
            raise ValueError("Task selection exceeds the standing permission policy")
    data = {"policy": asdict(policy), "selection": asdict(selection), "network": "deny"}
    for field in ("grants", "protections", "mandatory"):
        data["policy"][field] = sorted(data["policy"][field], key=lambda row: str(row["path"]))
    data["selection"]["roots"] = sorted(data["selection"]["roots"],
                                          key=lambda row: str(row["path"]))
    encoded = json.dumps(data, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return ExecutionPolicySnapshot(policy, selection, hashlib.sha256(encoded).hexdigest())


def access_at(snapshot: ExecutionPolicySnapshot, path) -> PathAccess:
    return _effective(snapshot.policy, snapshot.selection, _path(path))[0]


def is_anchor(snapshot, path):
    """Protect roots and ancestors whose renaming could move a protected subtree."""
    policy = snapshot.policy
    roots = policy.grants if snapshot.selection.mode == "inherit" else snapshot.selection.roots
    if any(path == rule.path for rule in roots):
        return True
    return any(rule.path.is_relative_to(path)
               for rule in (*policy.protections, *policy.mandatory))


def authorize_path(snapshot, path, operation) -> PermissionDecision:
    path = _path(path)
    access, reason = _effective(snapshot.policy, snapshot.selection, path)
    if operation in {"change_permissions", "take_ownership"}:
        return PermissionDecision(False, access, "security_management_denied")
    if operation not in _OPERATIONS:
        raise ValueError("Unknown resource operation")
    allowed = _RANK[access] >= _RANK[_OPERATIONS[operation]]
    if not allowed:
        return PermissionDecision(False, access, reason if reason != "granted" else "read_only")
    if operation in {"delete", "rename"} and is_anchor(snapshot, path):
        return PermissionDecision(False, access, "protected_anchor")
    return PermissionDecision(True, access, "allowed")


def still_allowed(snapshot, current: StandingPermissionPolicy) -> bool:
    """Compare each prefix partition; version changes alone do not revoke a Run.

Path rules are constant between prefix boundaries, so checking all old and new
boundaries catches new protected descendants as well as removed parent grants.
"""
    previous = snapshot.policy
    if (previous.environment_id, previous.agent_id) != (current.environment_id, current.agent_id):
        raise ValueError("Permission policy identity mismatch")
    if current.revision < previous.revision:
        raise ValueError("Permission policy revision went backwards")
    if not current.enabled:
        return False
    boundaries = {r.path for policy in (previous, current)
                  for rules in (policy.grants, policy.protections, policy.mandatory)
                  for r in rules} | {r.path for r in snapshot.selection.roots}
    newer = ExecutionPolicySnapshot(current, snapshot.selection, "comparison-only")
    for path in boundaries:
        old = access_at(snapshot, path)
        if _RANK[old] > _RANK[access_at(newer, path)]:
            return False
        # New anchor restrictions also revoke existing modify authority.
        if old == PathAccess.MODIFY and is_anchor(newer, path) and not is_anchor(snapshot, path):
            return False
    return True
