from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agent_runtime import (
    AgentConfig,
    EventEnvelope,
    RunRequest,
    RunSnapshot,
    RunState,
    RuntimeLimits,
    SchedulingProposal,
    TeamMessage,
    Usage,
)
from agent_runtime.runtime.run_journal import InMemoryRunJournal
from agent_runtime.runtime.team_collaboration import InMemoryTeamJournal


pytestmark = [pytest.mark.unit, pytest.mark.collaboration]


class NeverCalledPolicy:
    async def propose(self, snapshot, trigger):
        return SchedulingProposal(action="complete")


async def _journals():
    run_journal = InMemoryRunJournal()
    team_journal = InMemoryTeamJournal(run_journal)
    now = datetime.now(UTC)
    request = RunRequest(
        run_id="run",
        context_scope_id="conversation",
        input="work",
        agents=(AgentConfig(id="author", name="Author", system_prompt="work"),),
        policy=NeverCalledPolicy(),
    )
    await run_journal.create_run(
        request,
        RunSnapshot(
            run_id=request.run_id,
            context_scope_id=request.context_scope_id,
            state=RunState.RUNNING,
            reason_code=None,
            sequence=0,
            decision_count=0,
            no_progress_count=0,
            usage=Usage(),
            context_version=0,
            limits=RuntimeLimits(),
            started_at=now,
            finished_at=None,
        ),
    )
    return run_journal, team_journal


async def test_team_message_and_runtime_event_commit_with_independent_sequences():
    run_journal, team_journal = await _journals()
    message = TeamMessage(
        message_id="message-1",
        run_id="run",
        context_scope_id="conversation",
        sender_type="agent",
        sender_id="author",
        recipient_agent_ids=("reviewer",),
        channel="direct",
        content="Please review the boundary.",
        expects_reply=True,
        thread_id="thread-1",
    )
    event = EventEnvelope(
        event_id="event-1",
        run_id="run",
        context_scope_id="conversation",
        sequence=1,
        type="collaboration.message_created",
        payload={"sender_id": "author", "recipient_agent_ids": ["reviewer"]},
    )

    persisted, persisted_event = await team_journal.append_message(message, event)

    assert persisted.sequence == 1
    assert persisted_event.payload["team_sequence"] == 1
    assert (await team_journal.read_messages("conversation")).items == (persisted,)
    assert (await run_journal.read_events("run")).items == (persisted_event,)


async def test_consumption_resolution_and_interruption_are_auditable():
    run_journal, team_journal = await _journals()
    message, _ = await team_journal.append_message(
        TeamMessage(
            message_id="message-1",
            run_id="run",
            context_scope_id="conversation",
            sender_type="agent",
            sender_id="author",
            recipient_agent_ids=("reviewer",),
            channel="direct",
            content="Please review.",
            expects_reply=True,
            thread_id="thread-1",
        ),
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=1,
            type="collaboration.message_created",
            payload={},
        ),
    )
    consumed, _ = await team_journal.mark_consumed(
        message.message_id,
        "reviewer",
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=2,
            type="collaboration.message_consumed",
            payload={},
        ),
    )
    await team_journal.resolve_thread(
        "thread-1",
        "reviewer",
        EventEnvelope(
            run_id="run",
            context_scope_id="conversation",
            sequence=3,
            type="collaboration.thread_resolved",
            payload={"conclusion": "Boundary is safe."},
        ),
    )

    assert consumed.status == "consumed"
    assert consumed.consumed_by == ("reviewer",)
    page = await team_journal.read_messages("conversation")
    assert page.items[0].status == "resolved"
    assert [item.sequence for item in (await run_journal.read_events("run")).items] == [1, 2, 3]
    assert await team_journal.interrupt_run("run") == 0


async def test_failed_runtime_event_append_does_not_publish_team_message():
    run_journal, team_journal = await _journals()

    def fail(_event):
        raise RuntimeError("event store unavailable")

    run_journal._append_locked = fail  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="event store unavailable"):
        await team_journal.append_message(
            TeamMessage(
                run_id="run",
                context_scope_id="conversation",
                sender_type="agent",
                sender_id="author",
                content="not committed",
            ),
            EventEnvelope(
                run_id="run",
                context_scope_id="conversation",
                sequence=1,
                type="collaboration.message_created",
                payload={},
            ),
        )
    assert (await team_journal.read_messages("conversation")).items == ()
