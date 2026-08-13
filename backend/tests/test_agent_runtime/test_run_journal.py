from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from agent_runtime import (
    AgentConfig,
    EventEnvelope,
    RunRequest,
    RunResult,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    Usage,
)
from agent_runtime.runtime.run_journal import (
    EventSequenceConflictError,
    InMemoryRunJournal,
    sanitize_event_for_persistence,
)


pytestmark = [pytest.mark.unit, pytest.mark.runtime]


class NeverCalledPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="complete")


def _request(run_id: str = "run") -> RunRequest:
    return RunRequest(
        run_id=run_id,
        context_scope_id="scope",
        input="work",
        agents=(AgentConfig(id="agent", name="Agent", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )


def _snapshot(run_id: str = "run") -> RunSnapshot:
    return RunSnapshot(
        run_id=run_id,
        context_scope_id="scope",
        state=RunState.RUNNING,
        reason_code=None,
        sequence=0,
        decision_count=0,
        no_progress_count=0,
        usage=Usage(),
        context_version=0,
        limits=RuntimeLimits(),
        started_at=datetime.now(UTC),
        finished_at=None,
    )


async def test_in_memory_journal_orders_events_and_treats_identical_retry_as_idempotent():
    journal = InMemoryRunJournal()
    await journal.create_run(_request(), _snapshot())
    event = EventEnvelope(
        event_id="event-1",
        run_id="run",
        context_scope_id="scope",
        sequence=1,
        type="agent.token",
        payload={"content": "visible"},
    )

    await journal.append_event(event)
    await journal.append_event(event)

    page = await journal.read_events("run")
    assert page.items == (event,)
    assert page.next_sequence == 1
    assert page.last_sequence == 1
    assert page.terminal is False

    with pytest.raises(EventSequenceConflictError):
        await journal.append_event(replace(event, payload={"content": "different"}))
    with pytest.raises(EventSequenceConflictError):
        await journal.append_event(replace(event, event_id="event-2", sequence=3))


async def test_journal_terminal_event_and_state_commit_together():
    journal = InMemoryRunJournal()
    snapshot = _snapshot()
    await journal.create_run(_request(), snapshot)
    result = RunResult(
        run_id="run",
        context_scope_id="scope",
        state=RunState.COMPLETED,
        reason_code="completed",
        started_at=snapshot.started_at,
        finished_at=datetime.now(UTC),
        usage=Usage(),
        output="done",
    )
    terminal = EventEnvelope(
        run_id="run",
        context_scope_id="scope",
        sequence=1,
        type="system.run_completed",
        payload={"state": "completed", "output": "done"},
    )

    assert await journal.try_finish(result, terminal) is True
    assert await journal.try_finish(result, terminal) is False
    page = await journal.read_events("run")
    assert page.terminal is True
    assert page.items == (terminal,)


def test_persistence_sanitizer_removes_reasoning_and_credentials_but_keeps_visible_tokens():
    event = EventEnvelope(
        run_id="run",
        context_scope_id="scope",
        sequence=1,
        type="agent.token",
        payload={
            "token": "visible answer",
            "reasoning_content": "private chain",
            "nested": {"api_key": "secret-key", "content": "safe"},
        },
    )
    persisted = sanitize_event_for_persistence(event)
    assert persisted.payload["token"] == "visible answer"
    assert persisted.payload["reasoning_content"] == "[redacted]"
    assert persisted.payload["nested"] == {"api_key": "[redacted]", "content": "safe"}

    thinking = sanitize_event_for_persistence(
        replace(event, type="agent.thinking", payload={"agent_id": "agent", "thinking": "private"})
    )
    assert thinking.payload == {"agent_id": "agent", "redacted": True}
