"""Atomic in-memory reference adapter. Locks follow context then journal order."""

from copy import deepcopy

from ..core.ports import ContextConflictError
from ..core.run_types import ContextSnapshot
from .run_journal import sanitize_event_for_persistence


class InMemoryRunCompletion:
    def __init__(self, contexts, journal):
        self.contexts, self.journal = contexts, journal

    async def try_complete(self, delta, result, terminal_event):
        async with self.contexts._lock, self.journal._lock:
            if result.run_id in self.journal.finished:
                return None
            current = self.contexts._snapshots.get(result.context_scope_id) or ContextSnapshot(
                result.context_scope_id
            )
            if current.version != delta.expected_version:
                raise ContextConflictError("Context changed since this Run started")
            updated = ContextSnapshot(
                result.context_scope_id,
                current.version + 1,
                deepcopy(delta.messages),
                deepcopy(delta.blackboard),
                {**current.agent_memories, **delta.agent_memories},
                deepcopy(delta.continuation),
            )
            old_events = list(self.journal.events[result.run_id])
            old_ids = dict(self.journal._event_ids)
            try:
                self.journal._append_locked(sanitize_event_for_persistence(terminal_event))
                self.contexts._snapshots[result.context_scope_id] = updated
                self.journal.finished[result.run_id] = result
            except BaseException:
                self.journal.events[result.run_id] = old_events
                self.journal._event_ids = old_ids
                self.contexts._snapshots[result.context_scope_id] = current
                self.journal.finished.pop(result.run_id, None)
                raise
            return deepcopy(updated)
