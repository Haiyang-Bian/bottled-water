"""Continuation facts, completion fault boundaries, migration and cancellation races."""

import asyncio
import json
import sqlite3

import pytest

from agent_adapters.storage.session_lock import SessionBusyError, SessionLock
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.harness import ExecutionLimits
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_runtime.context.scope_store import InMemoryContextStore
from agent_runtime.runtime.completion import InMemoryRunCompletion
from agent_runtime.runtime.run_journal import InMemoryRunJournal
from agent_subsystems.context.continuation import JournalContinuationReader
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from test_harness_execution import ScriptedModel, Tool


def engine_for(model, store, *, cap=None, completion=None):
    return RuntimeEngine(
        agent_executor=AgentLoopExecutor(
            model_provider=model,
            tool_executor=Tool(),
            run_journal=store,
            execution_limits=ExecutionLimits(max_model_turns=cap),
        ),
        context_store=store,
        run_journal=store,
        completion_port=completion,
        continuation_reader=JournalContinuationReader(store),
    )


async def start(engine, scope="scope", prompt="Inspect and fix"):
    return await engine.start(
        RunRequest(
            scope, prompt, (AgentConfig("local", "Local", "Be accurate"),), SingleAgentPolicy()
        )
    )


async def test_failed_runs_rebuild_once_then_consume_only_with_success(tmp_path):
    path = tmp_path / "state.sqlite3"
    store = SQLiteStore(path)
    failed = engine_for(ScriptedModel(3), store, cap=2)
    first = await (await start(failed)).result()
    assert first.state.value == "failed"
    assert (await store.load("scope")).version == 0
    before = await JournalContinuationReader(store).read(await store.load("scope"))
    assert before == await JournalContinuationReader(store).read(await store.load("scope"))
    facts = json.loads(before["summary"])["observations"][0]
    assert facts["request"] == "Inspect and fix"
    assert len(facts["operations"]) == 2
    assert all(x["status"] == "completed" for x in facts["operations"])
    assert not (await JournalContinuationReader(store).read(await store.load("another")))["summary"]
    await failed.shutdown()
    store.close()
    store = SQLiteStore(path)

    class InspectModel(ScriptedModel):
        async def chat_stream(self, **kwargs):
            assert first.run_id in kwargs["system_prompt"]
            assert "Verify current state" in kwargs["system_prompt"]
            async for chunk in super().chat_stream(**kwargs):
                yield chunk

    success = engine_for(InspectModel(), store)
    second = await (await start(success, prompt="Continue after checking state")).result()
    assert second.state.value == "completed"
    snapshot = await store.load("scope")
    assert snapshot.version == 1 and len(snapshot.messages) == 2
    assert first.run_id in snapshot.continuation["cursors"]
    assert not (await JournalContinuationReader(store).read(snapshot))["summary"]
    assert store.run_result(first.run_id)["state"] == "failed"
    await success.shutdown()
    store.close()


@pytest.mark.parametrize("boundary", ["context", "event", "result"])
async def test_atomic_completion_rolls_back_all_components(tmp_path, boundary):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    trigger = {
        "context": "BEFORE INSERT ON continuation_metadata",
        "event": "BEFORE INSERT ON events WHEN NEW.body LIKE '%system.run_completed%'",
        "result": "BEFORE UPDATE OF result ON runs WHEN NEW.state='completed'",
    }[boundary]
    store.db.execute(f"CREATE TRIGGER injected {trigger} BEGIN SELECT RAISE(FAIL,'injected'); END")
    engine = engine_for(ScriptedModel(), store)
    result = await (await start(engine)).result()
    assert result.state.value == "failed" and result.reason_code == "event_store_error"
    assert (await store.load("scope")).version == 0
    assert store.db.execute("SELECT count(*) FROM continuation_metadata").fetchone()[0] == 0
    assert store.run_result(result.run_id)["state"] == "failed"
    events = (await store.read_events(result.run_id)).items
    assert (
        sum(e.type.startswith("system.run_") and e.type != "system.run_started" for e in events)
        == 1
    )
    assert not any(e.type == "system.run_completed" for e in events)
    await engine.shutdown()
    store.close()


async def test_completion_wins_cancel_only_after_transaction_has_started():
    contexts, journal = InMemoryContextStore(), InMemoryRunJournal()
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedCompletion(InMemoryRunCompletion):
        async def try_complete(self, *args):
            entered.set()
            await release.wait()
            return await super().try_complete(*args)

    engine = RuntimeEngine(
        agent_executor=AgentLoopExecutor(model_provider=ScriptedModel()),
        context_store=contexts,
        run_journal=journal,
        completion_port=DelayedCompletion(contexts, journal),
    )
    handle = await start(engine)
    await entered.wait()
    cancel = asyncio.create_task(handle.cancel())
    await asyncio.sleep(0)
    release.set()
    result = await asyncio.wait_for(cancel, 2)
    assert result.state.value == "completed"
    assert (await contexts.load("scope")).version == result.context_version == 1
    assert len(journal.finished) == 1
    await engine.shutdown()


def legacy_database(path):
    from test_local_environment import legacy
    legacy(path, 1)
    return {"id": "old"}


async def test_cancelled_started_operation_is_unknown_and_not_replayed(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    entered = asyncio.Event()

    class PendingTool(Tool):
        async def execute(self, call):
            self.calls += 1
            entered.set()
            await asyncio.Event().wait()

    tool = PendingTool()
    engine = RuntimeEngine(
        agent_executor=AgentLoopExecutor(
            model_provider=ScriptedModel(2), tool_executor=tool, run_journal=store
        ),
        context_store=store,
        run_journal=store,
    )
    handle = await start(engine)
    await entered.wait()
    result = await handle.cancel()
    assert result.state.value == "cancelled"
    summary = await JournalContinuationReader(store).read(await store.load("scope"))
    operation = json.loads(summary["summary"])["observations"][0]["operations"][0]
    assert operation["status"] == "unknown" and operation["started"]
    assert tool.calls == 1
    await engine.shutdown()
    store.close()


def test_migration_requires_session_locks_and_backs_up_wal_consistently(tmp_path):
    path = tmp_path / "state.sqlite3"
    session = legacy_database(path)
    with SessionLock(tmp_path / "locks", session["id"]):
        with pytest.raises(SessionBusyError):
            SQLiteStore(path)
    original = sqlite3.connect(path)
    original.execute("PRAGMA journal_mode=WAL")
    original.execute("INSERT INTO trusted VALUES('wal-only','now')")
    original.commit()
    migrated = SQLiteStore(path)
    assert migrated.schema_version == 5
    assert migrated.session(session["id"]) and migrated.is_trusted(tmp_path)
    with sqlite3.connect(migrated.backup_path) as backup:
        assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
        assert backup.execute("SELECT path FROM trusted WHERE path='wal-only'").fetchone()
    original.close()
    migrated.close()


def test_migration_failure_leaves_original_schema_and_backup(tmp_path):
    path = tmp_path / "state.sqlite3"
    legacy_database(path)
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE continuation_metadata(scope TEXT PRIMARY KEY, body TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        SQLiteStore(path)
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
    assert list(tmp_path.glob("*.bak"))
