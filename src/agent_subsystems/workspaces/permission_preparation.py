"""Process-local preparation lifecycle for explicit, quiescent permission changes.

Only a trusted host supplies the backend. This coordinator neither grants OS
access nor proves isolation. A lease may finish only after the driver confirms
its Job has drained. Cross-host coordination belongs to the persistent host
adapter; this object deliberately cannot be reused after a fork/restart.
"""

from enum import StrEnum
import os
from threading import RLock
from typing import Protocol
from uuid import uuid4

from agent_contracts.errors import OperationError
from agent_contracts.permissions import ExecutionPolicySnapshot


class PreparationState(StrEnum):
    NEW = "new"
    PREPARING = "preparing"
    READY = "ready"
    RETIRING = "retiring"
    RETIRED = "retired"
    REPAIR_REQUIRED = "repair_required"


class PreparationBackend(Protocol):
    """Backend journals intent before mutation and verifies cleanup before returning."""

    def prepare(self, generation: str, snapshot: ExecutionPolicySnapshot) -> None: ...

    def verify(self, generation: str) -> None: ...

    def retire(self, generation: str) -> None: ...


class PreparedPolicy:
    """One immutable permission generation; retirement is irreversible."""

    def __init__(self, snapshot: ExecutionPolicySnapshot, dependency_digest: str,
                 backend: PreparationBackend):
        if not dependency_digest:
            raise ValueError("A verified dependency fingerprint is required")
        self.snapshot = snapshot
        self.key = (snapshot.policy.environment_id, snapshot.policy.agent_id,
                    snapshot.digest, dependency_digest)
        self.generation = uuid4().hex
        self._backend = backend
        self._pid = os.getpid()
        self._lock = RLock()
        self._state = PreparationState.NEW
        self._active: dict[str, PreparationLease] = {}

    @property
    def state(self) -> PreparationState:
        with self._lock:
            return self._state

    @property
    def active_runs(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(sorted(self._active))

    def _require_owner(self):
        if os.getpid() != self._pid:
            raise OperationError("permission_process_mismatch", "Preparation belongs to another host")

    def prepare(self) -> None:
        with self._lock:
            self._require_owner()
            if self._state != PreparationState.NEW:
                raise OperationError("permission_generation_used", "Create a fresh preparation")
            self._state = PreparationState.PREPARING
            try:
                self._backend.prepare(self.generation, self.snapshot)
                self._backend.verify(self.generation)
            except BaseException:
                self._state = PreparationState.REPAIR_REQUIRED
                try:
                    self._backend.retire(self.generation)
                except BaseException as cleanup_error:
                    raise OperationError("permission_repair_required",
                                         "Preparation rollback was not verified") from cleanup_error
                self._state = PreparationState.RETIRED
                raise
            self._state = PreparationState.READY

    def acquire(self, run_id: str) -> "PreparationLease":
        with self._lock:
            self._require_owner()
            if self._state != PreparationState.READY:
                raise OperationError("permission_generation_unavailable",
                                     f"Preparation is {self._state}")
            if not run_id or run_id in self._active:
                raise OperationError("permission_run_conflict", "Run already registered or missing")
            try:
                self._backend.verify(self.generation)
            except BaseException:
                self._state = PreparationState.REPAIR_REQUIRED
                raise
            lease = PreparationLease(self, run_id)
            self._active[run_id] = lease
            return lease

    def retire(self) -> None:
        """Never report withdrawal while a managed execution may still hold access."""
        with self._lock:
            self._require_owner()
            if self._active:
                raise OperationError("permission_busy", "Stop all associated Runs before withdrawal")
            if self._state == PreparationState.RETIRED:
                return
            if self._state == PreparationState.NEW:
                self._state = PreparationState.RETIRED
                return
            self._state = PreparationState.RETIRING
            try:
                self._backend.retire(self.generation)
            except BaseException:
                self._state = PreparationState.REPAIR_REQUIRED
                raise
            self._state = PreparationState.RETIRED


class PreparationLease:
    """Host-side registration, never an argument accepted from model/worker JSON."""

    def __init__(self, preparation: PreparedPolicy, run_id: str):
        self._preparation = preparation
        self.run_id = run_id

    def check(self) -> None:
        prepared = self._preparation
        with prepared._lock:
            prepared._require_owner()
            if (prepared._state != PreparationState.READY
                    or prepared._active.get(self.run_id) is not self):
                raise OperationError("permission_lease_invalid", "Execution lease is no longer valid")

    def finish(self, *, job_drained: bool) -> None:
        prepared = self._preparation
        with prepared._lock:
            prepared._require_owner()
            if prepared._active.get(self.run_id) is not self:
                raise OperationError("permission_lease_invalid", "Execution lease already finished")
            if job_drained is not True:
                prepared._state = PreparationState.REPAIR_REQUIRED
                raise OperationError("isolation_cleanup_unconfirmed", "Job has not confirmed exit")
            del prepared._active[self.run_id]
