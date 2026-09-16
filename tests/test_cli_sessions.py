"""Session selection is driven by durable runs, not remembered UUIDs or opening times."""

import json
from dataclasses import asdict

import pytest

from agent_adapters.storage.sqlite import SQLiteStore
from agent_adapters.storage.session_lock import SessionBusyError, SessionLock
from agent_cli.main import parser
from agent_cli.selection import Choice, Selector
from agent_cli.sessions import SessionCatalogReader, SessionController, SessionHistoryReader
from agent_contracts.errors import ConfigurationError
from agent_subsystems.workspaces.paths import canonical_directory


@pytest.fixture
def saved(tmp_path):
    home, root = tmp_path / "home", canonical_directory(tmp_path)
    store = SQLiteStore(home / "state.sqlite3")
    store.trust(root)
    a, b, empty = [store.new_session(root) for _ in range(3)]
    for run_id, session, created, state, prompt in [
        ("run-a", a, "2026-01-01", "completed", "修复中文项目"),
        ("run-b", b, "2026-01-02", "failed", "继续检查"),
    ]:
        store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (
            run_id, session["id"], state, created, json.dumps({"input": prompt}),
            json.dumps({"output": "已保存答复", "reason_code": state}), 0,
        ))
    store.db.execute("UPDATE sessions SET updated='2099-01-01' WHERE id=?", (a["id"],))
    yield home, root, store, a, b, empty
    store.close()


def test_aliases_and_optional_resume():
    assert parser().parse_args(["-c"]).continue_session
    assert parser().parse_args(["-r"]).resume == ""
    assert parser().parse_args(["--resume", "id"]).resume == "id"
    assert parser().parse_args(["resume"]).resume_id == ""
    assert parser().parse_args(["resume", "id"]).resume_id == "id"


def test_catalog_ignores_empty_and_opening_times(saved):
    home, root, store, a, b, empty = saved
    reader = SessionCatalogReader(home)
    before = list(store.db.iterdump())
    rows = reader.list(root)
    assert [r.id for r in rows] == [b["id"], a["id"]]
    assert rows[1].title == "修复中文项目"
    assert reader.list(root / "another") == []
    assert list(store.db.iterdump()) == before
    with pytest.raises(ConfigurationError, match="Run ID"):
        reader.resolve("run-a")
    assert reader.resolve(a["id"])["cwd"] == str(root)
    with pytest.raises(ConfigurationError, match="不存在"):
        reader.resolve("missing")


def test_missing_catalog_and_draft_do_not_create_state(tmp_path):
    home = tmp_path / "missing"
    controller = SessionController(home, tmp_path, lambda *_: None)
    assert controller.catalog.list() == []
    controller.new()
    controller.close()
    assert not home.exists()


@pytest.mark.asyncio
async def test_busy_or_failed_switch_preserves_original(saved):
    home, root, store, a, b, _ = saved
    controller = SessionController(home, root, lambda *_: None)
    try:
        await controller.activate(a["id"])
        with SessionLock(home / "locks", b["id"]):
            with pytest.raises(SessionBusyError):
                await controller.activate(b["id"])
        assert controller.session["id"] == a["id"]
        with pytest.raises(SessionBusyError):
            with SessionLock(home / "locks", a["id"]):
                pass
        with pytest.raises(ConfigurationError, match="无法恢复"):
            await controller.activate(b["id"], cwd=root / "missing")
        assert controller.session["id"] == a["id"]
        with SessionLock(home / "locks", b["id"]):
            pass
    finally:
        controller.close()


def test_history_is_read_only_and_marks_partial_unknown(saved):
    from agent_runtime.core.run_types import EventEnvelope
    home, root, store, a, b, _ = saved
    store.db.execute("UPDATE runs SET result=NULL WHERE id='run-b'")
    for sequence, kind, payload in [
        (1, "agent.token", {"token": "尚未完成", "agent_message_id": "m"}),
        (2, "agent.thinking", {"thinking": "must not display"}),
        (3, "agent.tool_started", {"call_id": "c", "tool": "powershell"}),
    ]:
        event = EventEnvelope("run-b", b["id"], sequence, kind, payload)
        store.db.execute("INSERT INTO events VALUES(?,?,?,?)", (
            event.event_id, event.run_id, sequence, json.dumps(asdict(event), default=str),
        ))
    before = list(store.db.iterdump())
    turns, cursor = SessionHistoryReader(home).page(b["id"])
    assert "未完成输出" in turns[0].text()
    assert "结果未知" in turns[0].text()
    assert "尚未完成" in turns[0].output
    assert "must not display" not in turns[0].text()
    assert SessionHistoryReader(home).page(b["id"], before=cursor)[0] == []
    assert list(store.db.iterdump()) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("keys,expected", [
    ("\x1b[B\r", "b"), ("第二\r", "b"), ("\x1b", None), ("\r", "a"),
])
async def test_keyboard_selector(keys, expected):
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            selector = Selector([Choice("a", "第一"), Choice("b", "第二")])
            pipe.send_text(keys)
            assert await selector.run() == expected


@pytest.mark.asyncio
async def test_browsing_and_exit_do_not_construct_provider(tmp_path, monkeypatch):
    from agent_cli import app
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    monkeypatch.setattr(app.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(app.sys.stdout, "isatty", lambda: True)
    def forbidden(*_):
        pytest.fail("browsing constructed a model")
    monkeypatch.setattr(app.RunServices, "prepare", forbidden)
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            pipe.send_text("/help\r/exit\r")
            assert await app.chat(parser().parse_args([]), tmp_path / "home") == 0
    assert not (tmp_path / "home").exists()


async def test_global_restore_cd_and_draft_preserve_grants_and_history(saved):
    home, root, store, a, b, _ = saved
    elsewhere = root / "乙 B"
    elsewhere.mkdir()
    controller = SessionController(home, elsewhere, lambda db, p: db.trust(p))
    try:
        await controller.activate(a["id"])
        assert controller.session["cwd"] == str(root)
        controller.configure(cwd="乙 B")
        assert controller.session["workspace_version"] == 1
        assert controller.catalog.list()[0].id == b["id"]
        assert controller.catalog.list(elsewhere)[0].id == a["id"]
        before = list(store.db.iterdump())
        SessionHistoryReader(home).page(a["id"])
        assert list(store.db.iterdump()) == before
        with controller.execution():
            with pytest.raises(ConfigurationError, match="运行期间"):
                controller.configure(cwd=root)
        outside = root.parent / (root.name + "-outside")
        outside.mkdir()
        controller.configure(cwd=outside)
        assert controller.session["granted_roots"] == [str(root)]
        assert not store.is_trusted(canonical_directory(outside))
        controller.configure([outside])
        controller.configure(cwd=outside)
        saved_position = dict(controller.session)
        controller.new()
        assert controller.session["id"] is None
        assert controller.session["cwd"] == saved_position["cwd"]
        assert controller.session["granted_roots"] == saved_position["granted_roots"]
        await controller.activate(a["id"])
        assert controller.session == saved_position
    finally:
        controller.close()


async def test_failed_location_restore_keeps_original_and_history_readable(saved):
    home, root, store, a, b, _ = saved
    missing = root / "missing"
    with SessionLock(home / "locks", b["id"]) as lock:
        store.update_workspace(b["id"], cwd=missing, granted_roots=[root],
                               expected_version=0, lock=lock)
    controller = SessionController(home, root, lambda db, p: db.trust(p))
    try:
        await controller.activate(a["id"])
        with pytest.raises(ConfigurationError, match="只读查看"):
            await controller.activate(b["id"])
        assert controller.session["id"] == a["id"]
        assert SessionHistoryReader(home).page(b["id"])[0][0].request == "继续检查"
        await controller.activate(b["id"], cwd=root)
        assert controller.session["cwd"] == str(root)
    finally:
        controller.close()


async def test_control_transaction_failure_keeps_current_session_and_lock(saved):
    import sqlite3
    home, root, store, a, b, _ = saved
    target = root / "target"
    target.mkdir()
    controller = SessionController(home, root, lambda db, p: db.trust(p))
    try:
        await controller.activate(a["id"])
        original = dict(controller.session)
        store.db.execute("""CREATE TRIGGER fail_control BEFORE INSERT ON session_events
                            BEGIN SELECT RAISE(ABORT,'injected'); END""")
        with pytest.raises(sqlite3.IntegrityError):
            controller.configure(cwd=target)
        assert controller.session == original and store.session(a["id"]) == original
        with pytest.raises(sqlite3.IntegrityError):
            await controller.activate(b["id"], cwd=target)
        assert controller.session == original
        assert controller.lock.owns(home / "locks", a["id"])
        with SessionLock(home / "locks", b["id"]):
            pass
    finally:
        controller.close()
