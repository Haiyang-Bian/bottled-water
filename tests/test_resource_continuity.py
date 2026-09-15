"""L3 real native operations, cross-task references and authorization boundaries."""

import json
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from agent_adapters.local.files import LocalFiles
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.resources import (
    LocalSoftware,
    discover,
    management_operation,
    probe,
    index_directory,
)
from agent_adapters.storage.sqlite import SQLiteStore
from agent_adapters.storage.resources import SQLiteResources
from agent_adapters.storage.tasks import TaskCatalog
from agent_contracts.context import ContextBudget
from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.execution import (
    ExecutionContext,
    ExecutionLocation,
    ResourceGrant,
    WorkspaceSpec,
)
from agent_contracts.resources import ResourceRevision, ResourceContextItem
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_runtime.runtime.cancellation import CancellationScope, RunLease
from agent_subsystems.context.assembler import ContextAssembler
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.workspaces.resource_context import RunResourceContext
from agent_subsystems.workspaces.task_queries import query_parts, matches_date
from model_provider import ChatMessage, StreamChunk


@pytest.fixture
def catalog(tmp_path):
    store = SQLiteStore(tmp_path / "home" / "state.sqlite3")
    yield SQLiteResources(store)
    store.close()


def context(tmp_path):
    return ExecutionContext(
        "run",
        "scope",
        "local",
        ResourceGrant(WorkspaceSpec((tmp_path,)), frozenset({"files", "process"})),
        time.monotonic() + 60,
        CancellationScope(),
        RunLease("run"),
        ExecutionLocation(tmp_path),
    )


async def test_software_real_arguments_outputs_changes_and_no_grant(catalog, tmp_path):
    access = catalog.access()
    record = catalog.save(access, ResourceRevision("Python", sys.executable, "software"))
    driver = LocalProcessDriver(Redactor())
    software = LocalSoftware(driver)
    try:
        cfg = await software.verify(record, "python", tmp_path, management_operation(10))
        assert "Python" in cfg.version and cfg.sha256
        catalog.save_software(access, record.id, 1, cfg)
        output = tmp_path / "结果 space.txt"
        argv = ["-c", "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text('ok')", str(output)]
        result = await software.run(
            cfg, argv, context(tmp_path), outputs=[str(output), "missing.txt"]
        )
        assert result["exit_code"] == 0
        assert result["outputs"][0]["before"]["exists"] is False
        assert result["outputs"][0]["after"]["sha256"]
        assert result["outputs"][1]["after"]["exists"] is False
        unchanged = await software.run(
            cfg, ["-c", "pass"], context(tmp_path), outputs=[str(output)]
        )
        assert (
            unchanged["outputs"][0]["before"]["sha256"]
            == unchanged["outputs"][0]["after"]["sha256"]
        )
        with pytest.raises(OperationError, match="changed"):
            await software.run(replace(cfg, sha256="changed"), ["--version"], context(tmp_path))
        with pytest.raises(OperationError):
            await software.run(
                cfg, ["--version"], context(tmp_path), outputs=[str(tmp_path.parent / "outside")]
            )
        with pytest.raises(OperationError):
            await software.run(
                cfg, ["-c", "import time; time.sleep(30)"], context(tmp_path), timeout=0.2
            )
        assert not driver.jobs and not driver.processes
        catalog.set_status(access, record.id, 2, "disabled")
        with pytest.raises(OperationError):
            catalog.software(access, record.id)
        assert not catalog.store.is_trusted(tmp_path)
    finally:
        await driver.aclose()


async def test_index_metadata_limits_disabled_and_alias(catalog, tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    (root / "node_modules").mkdir()
    (root / "node_modules" / "noise.txt").write_text("noise")
    for name in ("甲.py", "乙.py", "丙.py"):
        (root / name).write_text("PRIVATE FILE CONTENT")
    files = LocalFiles(WorkspaceSpec((root,)), ExecutionLocation(root))
    result = await index_directory(
        files, root, catalog, catalog.access(), management_operation(), max_entries=2
    )
    assert result["partial"] and result["indexed"] == 2
    assert "PRIVATE FILE CONTENT" not in json.dumps(
        [asdict(r) for r in catalog.search(catalog.access())]
    )
    assert not catalog.search(catalog.access(), "noise")
    saved = catalog.search(catalog.access())[0]
    catalog.set_status(catalog.access(), saved.id, saved.revision, "disabled")
    await index_directory(files, root, catalog, catalog.access(), management_operation())
    assert saved.id not in {r.id for r in catalog.search(catalog.access())}
    alias = SimpleNamespace(
        content=SimpleNamespace(
            path=r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\python.exe"
        )
    )
    with pytest.raises(OperationError, match="WindowsApps"):
        await LocalSoftware(None).verify(alias, "python", root, management_operation())
    assert any(
        r["path"].lower() == str(__import__("pathlib").Path(sys.executable).resolve()).lower()
        for r in discover("python", root)
    )
    with pytest.raises(OperationError, match="deadline"):
        await probe(root, management_operation(-1))


def test_dates_are_local_bounded_and_query_is_not_a_session_id():
    now = datetime(2026, 9, 15, 8, tzinfo=timezone(timedelta(hours=8)))
    query, bounds = query_parts("昨天的实验", now=now)
    assert query == "实验"
    assert matches_date("2026-09-13T16:00:00+00:00", bounds)
    assert not matches_date("2026-09-14T16:00:00+00:00", bounds)
    assert query_parts("最近 3 天", now=now)[1][0].day == 13
    with pytest.raises(OperationError):
        query_parts("最近0天", now=now)
    with pytest.raises(OperationError):
        query_parts("", since="2026-09-16", until="2026-09-15", now=now)
    from agent_cli.main import parser

    args = parser().parse_args(["resume", "--query", "昨天的实验"])
    assert args.query == "昨天的实验" and not args.resume_id


async def test_actual_model_reference_manifest_and_history_separation(catalog, tmp_path):
    access = catalog.access()
    saved = catalog.save(access, ResourceRevision("实验文件", str(tmp_path / "A" / "data.csv")))
    session = catalog.store.new_session(tmp_path)
    captured = []

    class Model:
        async def chat_stream(self, messages, **kwargs):
            captured.append(messages)
            yield StreamChunk(content="done", finish_reason="stop")

    engine = RuntimeEngine(
        context_store=catalog.store,
        run_journal=catalog.store,
        agent_executor=AgentLoopExecutor(
            model_provider=Model(), resource_context=RunResourceContext(catalog, access, "实验文件")
        ),
    )
    try:
        handle = await engine.start(
            RunRequest(
                session["id"],
                "找实验文件",
                (AgentConfig("local", "Local", ""),),
                SingleAgentPolicy(),
                metadata={"resources_enabled": True},
            )
        )
        result = await handle.result()
        assert result.state.value == "completed"
        assert any(saved.id in m.content for m in captured[0])
        history = await catalog.store.load(session["id"])
        assert saved.id not in json.dumps(history.messages)
        events = (await catalog.store.read_events(handle.run_id)).items
        used = [e.payload for e in events if e.type == "agent.resources_used"]
        assert used[0]["items"][0]["id"] == saved.id
        assert catalog.pending(access) == 1
        tasks = TaskCatalog(catalog.store)
        assert tasks.search(access, "实验文件")[0]["id"] == session["id"]
        assert tasks.read(access, session["id"])["runs"][0]["assistant_statement"] == "done"
        with pytest.raises(ConfigurationError):
            tasks.search(replace(access, agent_id="foreign"), "实验")
        assert not catalog.store.is_trusted(tmp_path / "A")
    finally:
        await engine.shutdown()


def test_reference_budget_precedes_history_and_revocation(catalog, tmp_path):
    access = catalog.access()
    saved = catalog.save(access, ResourceRevision("实验", str(tmp_path / "a")))
    recalled = RunResourceContext(catalog, access, "实验")
    assert recalled.items()
    current = ChatMessage("user", "current")
    old = ChatMessage("user", "history")
    item = ResourceContextItem(saved.id, 1, None, "x" * 1000)
    prepared = ContextAssembler(ContextBudget(650)).prepare(
        [old, current], "", [], current_request=current, run_id="r", resource_items=[item]
    )
    assert prepared.messages == [old, current] and not prepared.resource_used
    message = ChatMessage(
        "tool", json.dumps({"result": {"resource_records": [asdict(saved)]}}), tool_call_id="c"
    )
    assert recalled.filter_results([message])[1]
    catalog.set_status(access, saved.id, 1, "disabled")
    assert not recalled.items()
    filtered, used, _ = recalled.filter_results([message])
    assert not used and saved.id not in filtered[0].content and filtered[0].tool_call_id == "c"


async def test_management_without_credentials_cas_and_explicit_grants(tmp_path, capsys):
    from agent_cli.main import parser
    from agent_cli.resources import command

    home = tmp_path / "state"

    async def run(*args):
        result = await command(
            parser().parse_args(["--json", "resources", *args]), home, cwd=tmp_path
        )
        return result, json.loads(capsys.readouterr().out)

    _, saved = await run("add", "--name", "中文资源", "--path", "data.txt")
    identifier = saved["id"]
    with pytest.raises(ConfigurationError, match="revision"):
        await run("disable", identifier)
    with pytest.raises(OperationError):
        await run("verify", identifier, "--revision", "1")
    (tmp_path / "data.txt").write_text("x")
    store = SQLiteStore(home / "state.sqlite3")
    from agent_subsystems.workspaces.paths import canonical_directory

    store.trust(canonical_directory(tmp_path), True)
    store.close()
    _, verified = await run("verify", identifier, "--revision", "1")
    assert verified["observation"]["facts"]["exists"]
    await run("disable", identifier, "--revision", "1")
    with pytest.raises(OperationError):
        await run("enable", identifier, "--revision", "1")


@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_resource_outbox_atomic_with_terminal_and_context(catalog, tmp_path, terminal):
    import sqlite3
    from agent_runtime.core.run_types import (
        RunResult,
        RunState,
        ContextDelta,
        Usage,
        utc_now,
        EventEnvelope,
    )

    session = catalog.store.new_session(tmp_path)
    rid = "fault"
    catalog.db.execute(
        "INSERT INTO runs VALUES(?,?,'running','2026-09-15',?,NULL,0)",
        (
            rid,
            session["id"],
            json.dumps({"metadata": {"resources_enabled": True, "memory_enabled": True}}),
        ),
    )
    result = RunResult(
        rid, session["id"], RunState(terminal), "test", utc_now(), utc_now(), Usage()
    )
    event = EventEnvelope(rid, session["id"], 1, "system.run_" + terminal, {})
    catalog.db.execute(
        "CREATE TRIGGER fail BEFORE INSERT ON resource_jobs BEGIN SELECT RAISE(ABORT,'fault'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        if terminal == "completed":
            await catalog.store.try_complete(ContextDelta(0, {}), result, event)
        else:
            await catalog.store.try_finish(result, event)
    assert not catalog.store.run_result(rid)
    assert not (await catalog.store.read_events(rid)).items
    assert (await catalog.store.load(session["id"])).version == 0
    assert not catalog.db.execute("SELECT 1 FROM memory_jobs").fetchone()


async def test_filtered_task_selector_never_auto_accepts_unique_candidate():
    import asyncio
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application import create_app_session
    from agent_cli.selection import choose_session

    calls = []
    item = SimpleNamespace(
        id="task", label=lambda: "昨天的实验", preview="完成", cwd="A", origin_root="A"
    )

    class Catalog:
        def list(self, root, query, **kwargs):
            calls.append((root, query))
            return [item]

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pending = asyncio.create_task(choose_session(Catalog(), query="昨天的实验"))
        await asyncio.sleep(0.1)
        assert not pending.done()
        pipe.send_text("\x1b")
        assert await asyncio.wait_for(pending, 5) is None
    assert calls == [(None, "昨天的实验")]
