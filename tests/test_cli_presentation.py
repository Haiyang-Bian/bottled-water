"""Public-event rendering, streamed redaction, editing and output-mode boundaries."""

import io
from datetime import timedelta

import pytest

from agent_cli.presentation import PresentationState
from agent_cli.rendering import PlainRenderer, TerminalRenderer, renderer_for
from agent_cli.terminal_text import TerminalTextStream
from agent_runtime.core.run_types import EventEnvelope, RunResult, RunState, Usage, utc_now
from agent_subsystems.observability.redaction import Redactor


def event(sequence, kind, **payload):
    return EventEnvelope("run", "scope", sequence, kind, payload)


def result(output):
    now = utc_now()
    return RunResult("run", "scope", RunState.COMPLETED, "completed", now,
                     now + timedelta(seconds=2), Usage(), output=output,
                     counters={"model_requests": 2, "tool_calls": 1})


def test_stream_redacts_split_secrets_and_escape_sequences():
    stream = TerminalTextStream(Redactor(["secret-value"]))
    out = "".join(stream.push(chunk) for chunk in [
        "hello sec", "ret-value \x1b", "]52;c;clipboard", "\x1b", "\\world\x1b[3", "1m!"
    ]) + stream.push("", final=True)
    assert out == "hello [redacted] world!"


def test_presentation_uses_call_start_and_real_clock():
    clock = [10.0]
    state = PresentationState(Redactor(), lambda: clock[0])
    call = event(1, "agent.tool_call", calls=[
        {"id": "a", "function": {"name": "git", "arguments": '{"args":["status"]}'}}
    ])
    state.consume(call)
    state.consume(call)
    assert state.tools["a"].started is None
    state.consume(event(2, "agent.tool_started", call_id="a", tool="git"))
    clock[0] = 12.5
    state.consume(event(3, "agent.tool_result", call_id="a", tool="git", success=False,
                        result={"exit_code": 2}, error="bad option"))
    assert state.tools["a"].elapsed == 2.5
    assert state.counters == {"model_requests": 0, "tool_rounds": 1, "tool_calls": 1}
    assert "失败" in state.tools["a"].summary()


def test_plain_final_answer_is_not_lost_after_intermediate_stream(capsys):
    renderer = PlainRenderer(Redactor())
    renderer.event(event(1, "agent.token", agent_message_id="one", token="检查中"))
    renderer.event(event(2, "message_stop", agent_message_id="one"))
    renderer.result(result("最终答复"))
    output = capsys.readouterr()
    assert "检查中" in output.out and "最终答复" in output.out
    assert "\x1b" not in output.out + output.err


def test_plain_final_and_private_events_are_not_duplicated(capsys):
    renderer = PlainRenderer(Redactor())
    renderer.event(event(1, "agent.thinking", thinking="private"))
    renderer.event(event(2, "agent.token", agent_message_id="one", token="最终答复"))
    renderer.event(event(3, "message_stop", agent_message_id="one"))
    renderer.result(result("最终答复"))
    output = capsys.readouterr().out
    assert output.count("最终答复") == 1 and "private" not in output


def test_rich_commits_long_stream_to_scrollback():
    from rich.console import Console
    capture = io.StringIO()
    console = Console(file=capture, width=40, height=10, no_color=True)
    renderer = TerminalRenderer(Redactor(), console=console)
    text = "# 中文标题\n\n```python\n" + "\n".join(f"value_{i} = {i}" for i in range(70)) + "\n```\n"
    try:
        for seq, offset in enumerate(range(0, len(text), 13), 1):
            renderer.event(event(seq, "agent.token", agent_message_id="one", token=text[offset:offset+13]))
        renderer.result(result(text))
    finally:
        renderer.close()
    output = capture.getvalue()
    assert "中文标题" in output and "value_0" in output and "value_69" in output
    assert "\x1b" not in output
    assert renderer.printed["one"] == len(text)


def test_json_never_loads_rich_or_prints_headers(capsys):
    import json
    renderer = renderer_for(Redactor(), json_mode=True, interactive=True)
    renderer.event(event(1, "agent.thinking", thinking="private"))
    renderer.event(event(2, "agent.token", token="hello"))
    renderer.result(result("hello"))
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["type"] for r in lines] == ["agent.token", "result"]


@pytest.mark.asyncio
@pytest.mark.parametrize("keys,expected", [
    ("first\x1b[200~\r\nsecond\x1b[201~\r", "first\nsecond"),
    ("first\x1b\rsecond\r", "first\nsecond"),
    ("first\nsecond\r", "first\nsecond"),
    ("/resu\t\r", "/resume"),
    ("/reso\t\r", "/resources"),
])
async def test_editor_multiline_paste_and_completion(tmp_path, keys, expected):
    from agent_cli.input import create_prompt
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            prompt = create_prompt(tmp_path)
            pipe.send_text(keys)
            assert await prompt.prompt_async("> ") == expected


def test_plain_and_no_color_modes(monkeypatch):
    from agent_cli.main import parser
    from agent_cli.ui import UserInterface
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("NO_COLOR", "1")
    assert not UserInterface(parser().parse_args([])).color
    assert not UserInterface(parser().parse_args(["--plain"])).rich
    monkeypatch.delenv("NO_COLOR")
    assert not UserInterface(parser().parse_args(["--no-color"])).color
    assert UserInterface(parser().parse_args([])).color
    assert isinstance(renderer_for(Redactor(), interactive=False), PlainRenderer)


@pytest.mark.asyncio
async def test_tool_viewer_reads_saved_results_without_writes(tmp_path, monkeypatch):
    import json
    from agent_cli import tool_details
    from agent_cli.sessions import SessionController
    from agent_adapters.storage.sqlite import SQLiteStore
    store = SQLiteStore(tmp_path / "state.sqlite3")
    session = store.new_session(tmp_path)
    store.db.execute("INSERT INTO runs VALUES(?,?,?,?,?,?,?)", (
        "run", session["id"], "completed", "2026-01-01", '{"input":"test"}', '{}', 2,
    ))
    from dataclasses import asdict
    for seq, kind, payload in [
        (1, "agent.tool_call", {"calls": [{"id": "call", "function": {
            "name": "powershell.run", "arguments": '{"script":"echo hello"}'}}]}),
        (2, "agent.tool_result", {"call_id": "call", "tool": "powershell.run", "success": True,
                                  "result": {"stdout": "hello", "truncated": True}}),
    ]:
        item = EventEnvelope("run", session["id"], seq, kind, payload)
        store.db.execute("INSERT INTO events VALUES(?,?,?,?)", (
            item.event_id, item.run_id, seq, json.dumps(asdict(item), default=str),
        ))
    before = list(store.db.iterdump())
    selections = iter(["run", "call"])
    async def choose(_):
        return next(selections)
    pages = []
    async def page(text, **_):
        pages.append(text)
        return True
    monkeypatch.setattr(tool_details.Selector, "run", choose)
    monkeypatch.setattr(tool_details, "pager", page)
    controller = SessionController(tmp_path, tmp_path)
    controller.session = session
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe, output=DummyOutput()):
            await tool_details.browse_tools(controller)
    assert "hello" in "\n".join(pages) and "不可恢复" in "\n".join(pages)
    assert list(store.db.iterdump()) == before
    assert controller.store is None
    store.close()
