"""Commit context CAS, continuation cursors, result and terminal event together."""

from agent_runtime.runtime.run_journal import sanitize_event_for_persistence
from db.session import AsyncSessionLocal

from .runtime_journal import SQLRunJournal, _is_terminal
from .runtime_store import SQLContextStore, _context_snapshot


class SQLRunCompletion:
    def __init__(self, session_factory=AsyncSessionLocal):
        self.factory = session_factory

    async def try_complete(self, delta, result, terminal_event):
        async with self.factory() as db, db.begin():
            run = await SQLRunJournal._locked_run(db, result.run_id)
            if run.context_scope_id != result.context_scope_id:
                raise ValueError("Run context scope mismatch")
            if _is_terminal(run.state):
                return None
            context = await SQLContextStore(self.factory).commit_in_session(
                db, result.context_scope_id, delta
            )
            await SQLRunJournal._append_locked(
                db, run, sanitize_event_for_persistence(terminal_event)
            )
            run.state = result.state.value
            run.reason_code = result.reason_code
            run.usage = result.usage.to_dict()
            run.context_version = result.context_version
            run.output = result.output
            run.finished_at = result.finished_at
            await db.flush()
            return _context_snapshot(context)
