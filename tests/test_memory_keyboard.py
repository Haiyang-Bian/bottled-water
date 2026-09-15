"""Memory selectors and forms use the existing non-fullscreen input surface."""

import asyncio
from types import SimpleNamespace

from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.application import create_app_session

from agent_cli.selection import Choice, Selector
from agent_cli.memory import content_values


async def test_memory_selection_filter_page_and_cancel():
    choices = [Choice(str(i), f"记忆 {i:02}", f"来源 {i}") for i in range(24)]
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        selector = Selector(choices, "记忆候选")
        task = asyncio.create_task(selector.run())
        pipe.send_text("23\r")
        assert await asyncio.wait_for(task, 5) == "23"
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        selector = Selector(choices, "选择记忆")
        task = asyncio.create_task(selector.run())
        pipe.send_text("\x1b")
        assert await asyncio.wait_for(task, 5) is None


def test_memory_applicability_path_is_not_file_authorization(tmp_path):
    args = SimpleNamespace(title="经验", body="已知信息", kind="experience", basic=None,
                           directory="中文 空格", global_scope=False, tag=None, alias=None)
    content = content_values(args, cwd=tmp_path)
    assert content.directory == str((tmp_path / "中文 空格").resolve())
    assert not (tmp_path / "中文 空格").exists()
