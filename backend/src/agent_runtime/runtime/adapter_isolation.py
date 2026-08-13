"""Cancellation boundaries for adapter calls."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .cancellation import CancellationScope, RunLease


T = TypeVar("T")


class AdapterTimeoutError(TimeoutError):
    pass


class AdapterNotCancellableError(RuntimeError):
    pass


async def run_cancellable_adapter(
    operation: Awaitable[T],
    *,
    cancellation: CancellationScope,
    lease: RunLease,
    timeout: float | None = None,
    terminate: Callable[[], Awaitable[None]] | None = None,
    has_external_side_effects: bool = False,
) -> T:
    """Run an adapter call and enforce an honest cancellation boundary.

    Side-effecting blocking adapters must provide a termination hook. Pure reads may
    continue outside the Run, but their result is rejected after lease revocation.
    """

    if has_external_side_effects and terminate is None:
        if asyncio.iscoroutine(operation):
            operation.close()
        raise AdapterNotCancellableError("adapter_not_cancellable")

    operation_task = asyncio.create_task(operation)
    cancel_task = asyncio.create_task(cancellation.wait())
    try:
        done, _ = await asyncio.wait(
            {operation_task, cancel_task},
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if operation_task in done:
            cancel_task.cancel()
            result = await operation_task
            lease.require_valid()
            return result
        if cancel_task in done:
            operation_task.cancel()
            if terminate is not None:
                await terminate()
            raise asyncio.CancelledError(cancellation.reason or "cancelled")
        operation_task.cancel()
        if terminate is not None:
            await terminate()
        raise AdapterTimeoutError("adapter_timeout")
    finally:
        cancel_task.cancel()
