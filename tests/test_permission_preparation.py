"""Lifecycle and failure ordering; simulated backends do not prove Windows isolation."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event

import pytest

from agent_contracts.errors import OperationError
from agent_contracts.permissions import PathPermission, StandingPermissionPolicy
from agent_subsystems.workspaces.permission_preparation import PreparedPolicy, PreparationState
from agent_subsystems.workspaces.permissions import freeze_policy


class Backend:
    def __init__(self):
        self.calls = []
        self.failure = None

    def prepare(self, generation, snapshot):
        self.calls.append(("prepare", generation))
        if self.failure == "prepare":
            raise RuntimeError("partial ACL preparation")

    def verify(self, generation):
        self.calls.append(("verify", generation))
        if self.failure == "verify":
            raise RuntimeError("changed root identity")

    def retire(self, generation):
        self.calls.append(("retire", generation))
        if self.failure == "retire":
            raise RuntimeError("ACL removal not verified")


@pytest.fixture
def policy_root():
    # Pure policy tests need a canonical path, not filesystem access or a temp directory.
    return Path(__file__).resolve().parents[1] / "var" / "logical-policy-root"


def preparation(policy_root, backend=None):
    snapshot = freeze_policy(StandingPermissionPolicy("environment", "local", 1,
                            (PathPermission(policy_root, "modify"),), enabled=True))
    return PreparedPolicy(snapshot, "dependency-fingerprint", backend or Backend())


def test_multiple_runs_reuse_one_preparation_then_retire_irreversibly(policy_root):
    backend = Backend()
    item = preparation(policy_root, backend)
    item.prepare()
    first, second = item.acquire("a"), item.acquire("b")
    first.check()
    second.check()
    assert item.active_runs == ("a", "b")
    with pytest.raises(OperationError, match="Stop all") as busy:
        item.retire()
    assert busy.value.code == "permission_busy"
    assert not any(op == "retire" for op, _ in backend.calls)
    first.finish(job_drained=True)
    with pytest.raises(OperationError):
        item.retire()
    second.finish(job_drained=True)
    item.retire()
    item.retire()
    assert item.state == PreparationState.RETIRED
    assert [op for op, _ in backend.calls].count("prepare") == 1
    assert [op for op, _ in backend.calls].count("retire") == 1
    for action in (item.prepare, lambda: item.acquire("c"), first.check, second.check):
        with pytest.raises(OperationError):
            action()
    replacement = preparation(policy_root)
    assert replacement.generation != item.generation
    assert replacement.key == item.key


@pytest.mark.parametrize("failure", ["prepare", "verify"])
def test_failed_preparation_is_compensated_without_exposing_ready(policy_root, failure):
    backend = Backend()
    backend.failure = failure
    item = preparation(policy_root, backend)
    with pytest.raises(RuntimeError):
        item.prepare()
    assert item.state == PreparationState.RETIRED
    assert backend.calls[-1][0] == "retire"
    with pytest.raises(OperationError):
        item.acquire("a")


def test_cleanup_failure_prevents_success_and_reuse_but_allows_verified_repair(policy_root):
    backend = Backend()
    item = preparation(policy_root, backend)
    item.prepare()
    backend.failure = "retire"
    with pytest.raises(RuntimeError):
        item.retire()
    assert item.state == PreparationState.REPAIR_REQUIRED
    with pytest.raises(OperationError):
        item.acquire("a")
    backend.failure = None
    item.retire()
    assert item.state == PreparationState.RETIRED


def test_failed_job_cleanup_keeps_registration_and_blocks_acl_removal(policy_root):
    backend = Backend()
    item = preparation(policy_root, backend)
    item.prepare()
    lease = item.acquire("a")
    with pytest.raises(OperationError) as failure:
        lease.finish(job_drained=False)
    assert failure.value.code == "isolation_cleanup_unconfirmed"
    assert item.active_runs == ("a",)
    with pytest.raises(OperationError):
        item.retire()
    assert not any(op == "retire" for op, _ in backend.calls)
    with pytest.raises(OperationError):
        lease.check()
    lease.finish(job_drained=True)
    item.retire()
    with pytest.raises(OperationError):
        lease.finish(job_drained=True)


def test_changed_identity_during_reuse_invalidates_existing_leases(policy_root):
    backend = Backend()
    item = preparation(policy_root, backend)
    item.prepare()
    first = item.acquire("a")
    backend.failure = "verify"
    with pytest.raises(RuntimeError):
        item.acquire("b")
    assert item.active_runs == ("a",)
    with pytest.raises(OperationError):
        first.check()
    with pytest.raises(OperationError):
        item.retire()
    first.finish(job_drained=True)
    item.retire()


def test_duplicate_run_and_foreign_process_are_rejected(policy_root, monkeypatch):
    item = preparation(policy_root)
    item.prepare()
    item.acquire("a")
    with pytest.raises(OperationError):
        item.acquire("a")
    monkeypatch.setattr("agent_subsystems.workspaces.permission_preparation.os.getpid", lambda: -1)
    with pytest.raises(OperationError) as error:
        item.acquire("b")
    assert error.value.code == "permission_process_mismatch"


def test_withdrawal_and_launch_are_serialized(policy_root):
    item = preparation(policy_root)
    item.prepare()
    barrier = Barrier(2)
    acquired = Event()

    def start():
        barrier.wait(timeout=5)
        try:
            lease = item.acquire("a")
        except OperationError as exc:
            return exc.code
        acquired.set()
        return lease

    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(start)
        barrier.wait(timeout=5)
        try:
            item.retire()
        except OperationError as exc:
            assert exc.code == "permission_busy"
        result = future.result(timeout=5)
    if acquired.is_set():
        assert item.state == PreparationState.READY
        result.finish(job_drained=True)
        item.retire()
    else:
        assert result == "permission_generation_unavailable"
    assert item.state == PreparationState.RETIRED


def test_partial_preparation_and_failed_compensation_remain_unusable(policy_root):
    class BrokenBackend(Backend):
        def prepare(self, generation, snapshot):
            raise RuntimeError("Partial ACL application")

        def retire(self, generation):
            raise RuntimeError("Compensation failed")

    item = preparation(policy_root, BrokenBackend())
    with pytest.raises(OperationError) as failure:
        item.prepare()
    assert failure.value.code == "permission_repair_required"
    assert item.state == PreparationState.REPAIR_REQUIRED
    with pytest.raises(OperationError):
        item.acquire("run")


def test_unconfirmed_job_remains_blocked_even_when_another_run_finishes(policy_root):
    item = preparation(policy_root)
    item.prepare()
    first, second = item.acquire("a"), item.acquire("b")
    with pytest.raises(OperationError):
        first.finish(job_drained=False)
    second.finish(job_drained=True)
    assert item.active_runs == ("a",)
    with pytest.raises(OperationError) as error:
        item.retire()
    assert error.value.code == "permission_busy"
    first.finish(job_drained=True)
    item.retire()


def test_retired_generation_cannot_come_back_when_recreating_same_policy(policy_root):
    item = preparation(policy_root)
    item.prepare()
    lease = item.acquire("run")
    lease.finish(job_drained=True)
    item.retire()
    replacement = preparation(policy_root)
    replacement.prepare()
    assert replacement.key == item.key
    assert replacement.generation != item.generation
    with pytest.raises(OperationError):
        lease.check()
    with pytest.raises(OperationError):
        item.acquire("again")
