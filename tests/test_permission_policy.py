"""Authority scenarios; these tests are not evidence of Windows isolation."""

from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from agent_contracts.permissions import (
    PathPermission as Rule,
    StandingPermissionPolicy as Policy,
    TaskPermissionSelection as Selection,
)
from agent_subsystems.workspaces.permissions import (
    PermissionLayoutError, access_at, authorize_path, freeze_policy, inheritable_roots,
    still_allowed,
)


@pytest.fixture
def scope():
    # No filesystem access or test-directory ACL changes are needed for policy logic.
    return Path(__file__).resolve().parents[1] / "var" / "工作 盘"


def standard(root):
    return Policy("environment", "local", 1,
                  (Rule(root / "Work", "modify"), Rule(root / "Archive", "read")),
                  (Rule(root / "Private", "deny"),),
                  (Rule(root / ".agenthub", "deny"),), enabled=True)


@pytest.mark.parametrize(("relative", "operation", "allowed"), [
    ("Work/project/code.py", "modify", True), ("Work/other/project", "create", True),
    ("Archive/raw.csv", "read", True), ("Archive/raw.csv", "modify", False),
    ("Archive/raw.csv", "delete", False), ("Private/key", "read", False),
    (".agenthub/state.sqlite3", "read", False), ("Work/project", "change_permissions", False),
    ("Work/project", "take_ownership", False), ("Work/project/a", "delete", True),
])
def test_inherited_work_and_protections(scope, relative, operation, allowed):
    snapshot = freeze_policy(standard(scope))
    assert authorize_path(snapshot, scope / relative, operation).allowed is allowed


def test_protection_cannot_be_overridden_by_nested_grant(scope):
    policy = standard(scope)
    policy = replace(policy, grants=(*policy.grants, Rule(scope / "Private/sub", "modify")),
                     protections=(*policy.protections, Rule(scope / "Private/sub", "read")))
    assert access_at(freeze_policy(policy), scope / "Private/sub/file") == "deny"


def test_custom_task_is_narrower_and_carries_protections(scope):
    snapshot = freeze_policy(standard(scope), Selection("custom", (Rule(scope / "Work", "read"),)))
    assert access_at(snapshot, scope / "Work/project") == "read"
    assert access_at(snapshot, scope / "Private/secret") == "deny"
    only = freeze_policy(standard(scope),
                         Selection("custom", (Rule(scope / "Work/project", "modify"),)))
    assert access_at(only, scope / "other") == "deny"
    assert access_at(only, scope / "Work/project/code") == "modify"
    for rule in (Rule(scope / "Archive", "modify"), Rule(scope.parent, "read")):
        with pytest.raises(ValueError, match="exceeds"):
            freeze_policy(standard(scope), Selection("custom", (rule,)))


def test_snapshots_do_not_gain_later_permissions(scope):
    policy = standard(scope)
    snapshot = freeze_policy(policy)
    expanded = replace(policy, revision=2, grants=(*policy.grants, Rule(scope.parent / "C", "modify")))
    assert still_allowed(snapshot, expanded)
    assert access_at(snapshot, scope.parent / "C") == "deny"
    assert freeze_policy(expanded).digest != snapshot.digest
    with pytest.raises(FrozenInstanceError):
        snapshot.policy.revision = 2


def test_new_descendant_protection_revokes_without_changing_root(scope):
    policy = standard(scope)
    snapshot = freeze_policy(policy)
    protected = replace(policy, revision=2,
                        protections=(*policy.protections, Rule(scope / "Work/project/new", "read")))
    assert not still_allowed(snapshot, protected)
    assert not still_allowed(snapshot, replace(policy, revision=2,
                                              grants=(Rule(scope / "Work", "read"),)))
    assert not still_allowed(snapshot, replace(policy, revision=2, enabled=False))


def test_unrelated_and_already_restricted_changes_do_not_cancel(scope):
    policy = standard(scope)
    snapshot = freeze_policy(policy,
                             Selection("custom", (Rule(scope / "Work/project", "read"),)))
    outside = replace(policy, revision=2,
                      protections=(*policy.protections, Rule(scope / "Work/other", "deny")))
    assert still_allowed(snapshot, outside)
    downgraded = replace(policy, revision=2, grants=(Rule(scope / "Work", "read"),))
    assert still_allowed(snapshot, downgraded)


def test_identity_and_revision_fail_closed(scope):
    policy = standard(scope)
    snapshot = freeze_policy(policy)
    for changed in (replace(policy, environment_id="other"), replace(policy, agent_id="other"),
                    replace(policy, revision=0)):
        with pytest.raises(ValueError):
            still_allowed(snapshot, changed)


def test_stable_fingerprint_and_distinct_custom_policy(scope):
    policy = standard(scope)
    reversed_rules = replace(policy, protections=tuple(reversed(policy.protections)))
    assert freeze_policy(policy).digest == freeze_policy(reversed_rules).digest
    assert freeze_policy(policy).digest != freeze_policy(
        policy, Selection("custom", (Rule(scope / "Work", "read"),))
    ).digest


def test_grant_roots_cannot_be_moved_or_deleted(scope):
    snapshot = freeze_policy(standard(scope))
    for path in (scope, scope / "Work", scope / "Archive"):
        assert not authorize_path(snapshot, path, "rename").allowed
        assert not authorize_path(snapshot, path, "delete").allowed
    assert authorize_path(snapshot, scope / "Work/work.txt", "modify").allowed


@pytest.mark.parametrize(("parent_access", "child_access"),
                         [("modify", "read"), ("modify", "deny"), ("read", "deny")])
def test_nested_restriction_rejected_before_preparation(scope, parent_access, child_access):
    policy = Policy("env", "local", 1, (Rule(scope, parent_access),),
                    (Rule(scope / "Archive", child_access),), enabled=True)
    with pytest.raises(PermissionLayoutError) as error:
        freeze_policy(policy)
    assert error.value.reason_code == "permission_layout_conflict"
    assert error.value.path == Rule(scope / "Archive", child_access).path
    assert "Move protected material" in str(error.value)


def test_mandatory_secret_below_broad_grant_cannot_be_silently_skipped(scope):
    with pytest.raises(PermissionLayoutError):
        freeze_policy(replace(standard(scope), grants=(Rule(scope, "modify"),)))


def test_read_parent_modify_child_follows_natural_inheritance(scope):
    policy = Policy("env", "local", 1,
                    (Rule(scope, "read"), Rule(scope / "Work", "modify")), enabled=True)
    assert inheritable_roots(freeze_policy(policy)) == policy.grants


def test_custom_selection_can_exclude_conflict_or_lower_all_selected_rights(scope):
    policy = Policy("env", "local", 1, (Rule(scope, "modify"),),
                    (Rule(scope / "Archive", "read"),), enabled=True)
    snapshot = freeze_policy(policy, Selection("custom", (Rule(scope / "Work", "modify"),)))
    assert inheritable_roots(snapshot) == (Rule(scope / "Work", "modify"),)
    readonly = freeze_policy(policy, Selection("custom", (Rule(scope, "read"),)))
    assert inheritable_roots(readonly) == (Rule(scope, "read"),)


def test_same_root_protection_and_redundant_grants_compile_without_exceptions(scope):
    policy = Policy("env", "local", 1, (Rule(scope, "modify"),
                    Rule(scope / "Work", "modify")), (Rule(scope, "read"),), enabled=True)
    assert inheritable_roots(freeze_policy(policy)) == (Rule(scope, "read"),)


@pytest.mark.parametrize("custom", [False, True])
def test_explicit_read_child_cannot_silently_become_writable(scope, custom):
    roots = (Rule(scope, "modify"), Rule(scope / "Archive", "read"))
    policy = Policy("env", "local", 1, roots[:1] if custom else roots, enabled=True)
    selection = Selection("custom", roots) if custom else Selection()
    with pytest.raises(PermissionLayoutError):
        freeze_policy(policy, selection)


@pytest.mark.parametrize("value", ["relative", "../relative"])
def test_relative_rules_are_rejected(value):
    with pytest.raises(ValueError):
        Rule(Path(value), "read")


def test_invalid_or_mutable_policy_inputs(scope):
    rules = [Rule(scope, "modify")]
    policy = Policy("env", "local", 1, rules, enabled=True)
    rules.clear()
    assert len(policy.grants) == 1
    with pytest.raises(ValueError):
        replace(policy, grants=(Rule(scope, "deny"),))
    with pytest.raises(ValueError):
        replace(policy, protections=(Rule(scope, "modify"),))
    with pytest.raises(ValueError):
        Selection("inherit", (Rule(scope, "read"),))
    with pytest.raises(ValueError):
        Selection("custom")
    with pytest.raises(ValueError):
        freeze_policy(replace(policy, enabled=False))
    with pytest.raises(ValueError):
        replace(freeze_policy(policy), network="allow")
