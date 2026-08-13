"""Cancellation and write-lease primitives for a single Runtime run."""

from __future__ import annotations

import asyncio


class RunCancelledError(asyncio.CancelledError):
    pass


class RunLeaseRevokedError(RuntimeError):
    pass


class CancellationScope:
    def __init__(self) -> None:
        self._event = asyncio.Event()
        self._reason: str | None = None

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    @property
    def reason(self) -> str | None:
        return self._reason

    def cancel(self, reason: str) -> bool:
        if self._event.is_set():
            return False
        self._reason = reason
        self._event.set()
        return True

    async def wait(self) -> str:
        await self._event.wait()
        return self._reason or "cancelled"

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise RunCancelledError(self._reason or "cancelled")


class RunLease:
    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._valid = True

    @property
    def valid(self) -> bool:
        return self._valid

    def revoke(self) -> None:
        self._valid = False

    def require_valid(self) -> None:
        if not self._valid:
            raise RunLeaseRevokedError(f"Run lease has been revoked: {self.run_id}")
