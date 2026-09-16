"""Immutable permission inputs. Paths must be resolved by the local adapter first.

These types describe authority; constructing one does not grant Windows access.
No model-provided identity or approval field may be used to construct a policy.
"""

from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path


class PathAccess(StrEnum):
    DENY = "deny"
    READ = "read"
    MODIFY = "modify"


@dataclass(frozen=True)
class PathPermission:
    path: Path
    access: PathAccess

    def __post_init__(self):
        path = Path(os.path.normcase(str(self.path)))
        if not path.is_absolute() or ".." in path.parts or "\0" in str(path):
            raise ValueError("Permission paths must be absolute and canonical")
        object.__setattr__(self, "path", path)
        object.__setattr__(self, "access", PathAccess(self.access))


@dataclass(frozen=True)
class StandingPermissionPolicy:
    environment_id: str
    agent_id: str
    revision: int
    grants: tuple[PathPermission, ...] = ()
    protections: tuple[PathPermission, ...] = ()
    mandatory: tuple[PathPermission, ...] = ()
    enabled: bool = False

    def __post_init__(self):
        if not self.environment_id or not self.agent_id or self.revision < 0:
            raise ValueError("Policy requires host-bound identity and a nonnegative revision")
        for field in ("grants", "protections", "mandatory"):
            rules = tuple(getattr(self, field))
            if not all(isinstance(rule, PathPermission) for rule in rules):
                raise TypeError("Expected PathPermission records")
            if len({rule.path for rule in rules}) != len(rules):
                raise ValueError("Duplicate paths must be resolved by the management transaction")
            if any(rule.access == PathAccess.DENY for rule in rules) and field == "grants":
                raise ValueError("Base grants must allow read or modify")
            if any(rule.access == PathAccess.MODIFY for rule in rules) and field != "grants":
                raise ValueError("Protections may only restrict to read or deny")
            object.__setattr__(self, field, rules)


@dataclass(frozen=True)
class TaskPermissionSelection:
    mode: str = "inherit"
    roots: tuple[PathPermission, ...] = ()

    def __post_init__(self):
        roots = tuple(self.roots)
        if self.mode not in {"inherit", "custom"}:
            raise ValueError("Unknown permission selection mode")
        if (self.mode == "inherit" and roots) or (self.mode == "custom" and not roots):
            raise ValueError("Only custom selection may specify a nonempty set of roots")
        if not all(isinstance(rule, PathPermission) and rule.access != PathAccess.DENY
                   for rule in roots):
            raise ValueError("Task roots must allow read or modify")
        if len({rule.path for rule in roots}) != len(roots):
            raise ValueError("Duplicate task permission paths")
        object.__setattr__(self, "roots", roots)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    access: PathAccess
    reason_code: str


@dataclass(frozen=True)
class ExecutionPolicySnapshot:
    policy: StandingPermissionPolicy
    selection: TaskPermissionSelection
    digest: str
    network: str = "deny"

    def __post_init__(self):
        if self.network != "deny":
            raise ValueError("L4a does not support tool network access")
