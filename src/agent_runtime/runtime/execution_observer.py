"""Translate typed executor observations into Kernel-owned activity and accounting."""

import time

from agent_contracts.harness import ExecutionStopped
from agent_runtime.core.run_types import Usage


class KernelExecutionObserver:
    def __init__(self, kernel, execution_id):
        self.kernel, self.execution_id = kernel, execution_id
        self.started = {}

    async def phase_started(self, phase_id, phase, deadline):
        k = self.kernel
        k.lease.require_valid()
        self.started[phase_id] = (phase, time.monotonic())
        k._watchdog.phase_started(phase_id, phase, deadline)
        await k._emit("execution.phase_started", {
            "execution_id": self.execution_id, "phase_id": phase_id,
            "phase": phase, "timeout_seconds": max(0, deadline - time.monotonic()),
        })

    async def phase_finished(self, phase_id):
        k = self.kernel
        phase, started = self.started.pop(phase_id, ("unknown", time.monotonic()))
        k._watchdog.phase_finished(phase_id)
        if k.lease.valid:
            await k._emit("execution.phase_finished", {
                "execution_id": self.execution_id, "phase_id": phase_id,
                "phase": phase, "elapsed_seconds": time.monotonic() - started,
            })

    async def usage_reported(self, request_id, usage, counters):
        k = self.kernel
        k.lease.require_valid()
        key = (self.execution_id, request_id)
        k._execution_counters[self.execution_id] = dict(counters)
        if key in k._accounted_requests:
            return
        k._accounted_requests.add(key)
        previous = k._execution_usage.get(self.execution_id, Usage())
        current = Usage(**usage)
        k.usage.add(Usage(
            max(0, current.prompt_tokens - previous.prompt_tokens),
            max(0, current.completion_tokens - previous.completion_tokens), current.estimated,
        ))
        k._execution_usage[self.execution_id] = current
        k._execution_counters[self.execution_id] = dict(counters)
        await k._emit("execution.usage", {
            "execution_id": self.execution_id, "request_id": request_id,
            "usage": current.to_dict(), "run_usage": k.usage.to_dict(), "counters": counters,
        })
        if k._watchdog.check_tokens(k.usage.total_tokens):
            raise ExecutionStopped("token_budget_exhausted")
