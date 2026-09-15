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
    a, b, empty = [store.new_session(root) for _ in range(3)]
    for run_id, session, created, state, prompt in [
        ("run-a", a, "2026-01-01", "completed", "修复中文项目"),
        ("run-b", b, "2026-01-02", "failed", "继续检查"),
    ]:
        store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (
            run_id, session["id"], state, created, json.dumps({"input": prompt}),
            json.dumps({"output": "已保存答复", "reason_code": state}), 0,
        ))
    store.set_directories(a["id"], [])
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
        reader.resolve("run-a", root)
    with pytest.raises(ConfigurationError, match="其他目录"):
        reader.resolve(a["id"], root / "other")
    with pytest.raises(ConfigurationError, match="不存在"):
        reader.resolve("missing", root)


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
        def reject(*_):
            raise ConfigurationError("declined")
        controller.ensure_trusted = reject
        with pytest.raises(ConfigurationError, match="declined"):
            await controller.activate(b["id"])
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
