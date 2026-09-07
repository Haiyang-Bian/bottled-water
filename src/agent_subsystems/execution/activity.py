"""Bounded phases and a streaming guard for accidental provider protocol frames."""

import asyncio
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from agent_contracts.harness import ExecutionStopped


@asynccontextmanager
async def execution_phase(observer, phase, timeout, deadline=None):
    phase_id = uuid4().hex
    expires = min(time.monotonic() + timeout, deadline or float("inf"))
    if observer:
        await observer.phase_started(phase_id, phase, expires)
    try:
        async with asyncio.timeout_at(expires):
            yield
    except TimeoutError as exc:
        reason = "wall_time_exceeded" if expires == deadline else f"{phase}_timeout"
        raise ExecutionStopped(reason) from exc
    finally:
        if observer:
            await observer.phase_finished(phase_id)


class ProtocolFrameGuard:
    """Hold ambiguous line prefixes, not ordinary prose or fenced examples."""

    markers = ("<｜｜DSML｜｜tool_calls", "<｜DSML｜tool_calls", "<tool_calls>")

    def __init__(self):
        self.pending = ""
        self.fenced = False
        self.line_start = True
        self.invalid = False

    def push(self, text, *, final=False):
        self.pending += text
        output = []
        while self.pending and not self.invalid:
            if self.line_start:
                stripped = self.pending.lstrip(" \t")
                if not final and ("```".startswith(stripped)
                                  or any(m.startswith(stripped) for m in self.markers)):
                    break
                if stripped.startswith("```"):
                    self.fenced = not self.fenced
                elif not self.fenced and any(stripped.startswith(m) for m in self.markers):
                    self.invalid = True
                    self.pending = ""
                    break
                self.line_start = False
            character, self.pending = self.pending[0], self.pending[1:]
            output.append(character)
            if character == "\n":
                self.line_start = True
        return "".join(output)
