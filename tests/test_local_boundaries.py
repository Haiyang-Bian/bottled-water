"""Failure and boundary cases that must differ from successful local execution."""

import json
import os
import sqlite3
from types import SimpleNamespace

import pytest

from agent_adapters.local.files import LocalFiles
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.config import Profile
from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.execution import WorkspaceSpec
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine, RunState
from agent_runtime.core.run_types import (
    AgentExecutionResult,
    ContextSnapshot,
    EventEnvelope,
    RunResult,
    Usage,
    utc_now,
)
from agent_runtime.core.types import AgentReport, AgentState, AgentWill
from agent_subsystems.context.local import LocalContextProvider
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy


def test_interactive_trust_is_asked_once_across_restarts(tmp_path, monkeypatch, capsys):
    import io
    from agent_cli.host import ensure_trusted

    database = tmp_path / "state.sqlite3"
    store = SQLiteStore(database)
    try:
        monkeypatch.setattr("sys.stdin", io.StringIO("y\n"))
        ensure_trusted(store, tmp_path, True)
        assert "PowerShell/Git" in capsys.readouterr().err
    finally:
        store.close()
    store = SQLiteStore(database)
    try:
        monkeypatch.setattr("sys.stdin", io.StringIO(""))
        ensure_trusted(store, tmp_path, True)
        assert not capsys.readouterr().err
    finally:
        store.close()


@pytest.mark.asyncio
async def test_history_budget_preserves_whole_recent_turn_and_current_input(tmp_path):
    provider = LocalContextProvider(WorkspaceSpec(tmp_path), max_history_chars=12)
    history = tuple(
        {"role": role, "content": text}
        for role, text in [
            ("user", "old question"),
            ("assistant", "old answer"),
            ("user", "new"),
            ("assistant", "yes"),
        ]
    )
    request = SimpleNamespace(
        context_snapshot=ContextSnapshot("scope", messages=history),
        base_user_prompt="now",
        base_system_prompt="system",
    )
    context = await provider.build_agent_context(request)
    assert [m["content"] for m in context.messages] == ["new", "yes", "now"]
    assert context.diagnostics["dropped_turns"] == 1
    request.base_user_prompt = "large current request that must survive"
    context = await provider.build_agent_context(request)
    assert context.messages == [{"role": "user", "content": request.base_user_prompt}]
    assert context.diagnostics["current_request_over_budget"]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "100", 1.5])
def test_invalid_profile_limits_are_configuration_errors(value):
    with pytest.raises(ConfigurationError):
        Profile("deepseek", "deepseek-chat", "env:TEST_KEY", max_tokens=value)


@pytest.mark.asyncio
async def test_search_pagination_stays_bounded_without_losing_matches(tmp_path):
    (tmp_path / "large.txt").write_text(("命中" * 1000 + "\n") * 200, encoding="utf-8")
    files = LocalFiles(WorkspaceSpec(tmp_path))
    lines, offset = [], 0
    while True:
        page = await files.search("命中", limit=1000, offset=offset)
        assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= 65536
        lines.extend(row["line"] for row in page["matches"])
        if page["next_offset"] is None:
            break
        assert page["truncated"] and page["next_offset"] > offset
        offset = page["next_offset"]
    assert lines == list(range(1, 201))


@pytest.mark.skipif(os.name != "nt", reason="Windows junctions")
@pytest.mark.asyncio
async def test_junction_cannot_expand_file_grant(tmp_path):
    import _winapi

    root, outside = tmp_path / "allowed", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.txt").write_text("outside resource", encoding="utf-8")
    junction = root / "linked"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        with pytest.raises(OperationError):
            await LocalFiles(WorkspaceSpec(root)).read("linked/secret.txt")
        allowed = LocalFiles(WorkspaceSpec(root, (outside,)))
        assert (await allowed.read("linked/secret.txt"))["content"] == "outside resource"
    finally:
        junction.rmdir()  # Remove only this junction, never its target.


@pytest.mark.asyncio
async def test_terminal_write_failure_rolls_back_event_and_can_be_recovered(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3")
    request = RunRequest("scope", "task", (AgentConfig("a", "A", ""),), SingleAgentPolicy())
    try:
        await store.create_run(request, SimpleNamespace(state=RunState.CREATED, started_at=None))
        store.db.executescript("""
            CREATE TRIGGER fail_finish BEFORE UPDATE OF result ON runs
            BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END;
        """)
        result = RunResult(
            request.run_id, "scope", RunState.COMPLETED, "completed", utc_now(), utc_now(), Usage()
        )
        event = EventEnvelope(request.run_id, "scope", 1, "system.run_completed", {})
        with pytest.raises(sqlite3.DatabaseError):
            await store.try_finish(result, event)
        assert not (await store.read_events(request.run_id)).items
        assert store.run_result(request.run_id) is None
        store.db.execute("DROP TRIGGER fail_finish")
        assert await store.try_finish(result, event)
        assert not await store.try_finish(result, event)
        assert len((await store.read_events(request.run_id)).items) == 1
    finally:
        store.close()


@pytest.mark.asyncio
async def test_kernel_returns_failure_when_sqlite_cannot_save_terminal(tmp_path):
    class Executor:
        async def execute(self, request, **kwargs):
            return AgentExecutionResult(
                "a",
                AgentReport("a", AgentState.COMPLETED, AgentWill.COMPLETE),
                output="visible result",
            )

    store = SQLiteStore(tmp_path / "state.sqlite3", Redactor(["known-secret"]))
    store.db.executescript("""
        CREATE TRIGGER fail_finish BEFORE UPDATE OF result ON runs
        BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END;
    """)
    engine = RuntimeEngine(agent_executor=Executor(), context_store=store, run_journal=store)
    try:
        handle = await engine.start(
            RunRequest("scope", "known-secret", (AgentConfig("a", "A", ""),), SingleAgentPolicy())
        )
        result = await handle.result()
        assert result.state == RunState.FAILED and result.reason_code == "event_store_error"
        assert "known-secret" not in str(list(store.db.iterdump()))
    finally:
        await engine.shutdown()
        store.close()
