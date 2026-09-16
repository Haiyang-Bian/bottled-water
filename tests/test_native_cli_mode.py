"""Release entrypoints cannot implicitly enable LPAC or inherit an elevated token."""

from types import SimpleNamespace

import pytest

from agent_adapters.local import user_execution
from agent_cli import app, sandbox
from agent_cli.main import dispatch, parser
from agent_cli.resources import command as resources_command
from agent_cli.sessions import SessionController
from agent_contracts.errors import ConfigurationError


async def test_elevated_host_rejected_before_run_or_tool_management(tmp_path, monkeypatch):
    monkeypatch.setattr(user_execution, "is_elevated", lambda: True)
    home = tmp_path / "home"
    controller = SessionController(home, tmp_path)
    try:
        with pytest.raises(ConfigurationError, match="管理员"):
            controller.materialize()
        services = app.RunServices(parser().parse_args([]), home)
        with pytest.raises(ConfigurationError, match="管理员"):
            services.prepare()
        with pytest.raises(ConfigurationError, match="管理员"):
            await resources_command(parser().parse_args(["resources", "index", str(tmp_path)]), home)
        assert not home.exists()
    finally:
        controller.close()


async def test_paused_initialization_does_not_launch_component(tmp_path, monkeypatch):
    def forbidden(*_):
        pytest.fail("paused initialization reached Windows setup")
    monkeypatch.setattr(sandbox, "require_platform", forbidden)
    home = tmp_path / "home"
    for operation in ("setup", "self-test"):
        with pytest.raises(ConfigurationError, match="暂缓"):
            await sandbox.command(SimpleNamespace(operation=operation, json=True), home)
    assert not home.exists()


async def test_windows_selection_fails_before_model_and_preserves_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTHUB_HOME", str(home))
    with pytest.raises(ConfigurationError, match="暂缓"):
        await dispatch(parser().parse_args(["--sandbox", "windows", "-p", "test"]))
    assert not home.exists()


async def test_native_reference_directories_are_not_permissions(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setenv("AGENTHUB_HOME", str(home))
    with pytest.raises(ConfigurationError, match="暂缓"):
        await dispatch(parser().parse_args(["--read-dir", str(tmp_path), "-p", "test"]))
    assert not home.exists()
