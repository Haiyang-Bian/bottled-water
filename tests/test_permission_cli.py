"""Permission CLI projections and task transitions without model credentials."""

from dataclasses import replace
import os
from types import SimpleNamespace

import pytest

from agent_adapters.storage.permissions import SQLitePermissions
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.permissions import task_view, command
from agent_cli.sessions import SessionController
from agent_contracts.permissions import PathPermission


def configured(tmp_path):
    work, home = tmp_path / "Work", tmp_path / "Home"
    work.mkdir()
    from agent_subsystems.workspaces.paths import canonical_directory
    work = canonical_directory(work)
    store = SQLiteStore(home / "state.sqlite3")
    authority = SQLitePermissions(store)
    policy = replace(authority.load(), enabled=True, grants=(PathPermission(work, "modify"),))
    transition, _, _ = authority.begin(policy, 0)
    authority.transition(transition, "retiring")
    authority.commit(transition)
    store.trust(work)
    old = store.new_session(work)
    store.close()
    return work, home, old


def no_trust(*_):
    raise AssertionError("Inherited permissions must not ask for old trust")


def test_draft_view_does_not_create_state(tmp_path):
    controller = SessionController(tmp_path / "absent", tmp_path, no_trust)
    assert task_view(controller)["policy_revision"] == 0
    assert not controller.home.exists()
    assert controller.store is None


async def test_default_inherit_restart_and_explicit_mode_conversion(tmp_path):
    work, home, old = configured(tmp_path)
    controller = SessionController(home, work, no_trust)
    try:
        controller.default_mode()
        controller.configure()
        assert controller.session["execution_mode"] == "windows_lpac"
        controller.materialize()
        identifier = controller.session["id"]
        controller.new()
        assert controller.session["execution_mode"] == "windows_lpac"
        await controller.activate(identifier)
        assert task_view(controller)["policy_revision"] == 1
        await controller.activate(old["id"])
        assert controller.session["execution_mode"] == "current_user"
        await controller.activate(old["id"], execution_options={"sandbox": "windows"})
        assert controller.session["execution_mode"] == "windows_lpac"
        assert controller.session["workspace_version"] == 1
    finally:
        controller.close()


def test_custom_selection_only_narrows_and_failed_cd_keeps_task(tmp_path):
    work, home, _ = configured(tmp_path)
    inner = work / "narrow"
    inner.mkdir()
    other = tmp_path / "Private"
    other.mkdir()
    controller = SessionController(home, work, no_trust)
    try:
        controller.default_mode()
        controller.configure(cwd=str(inner), execution_options={
            "permissions": "custom", "read_dir": [str(inner)], "write_dir": [],
        })
        original = dict(controller.session)
        with pytest.raises(Exception, match="permissions grant"):
            controller.configure([str(other)])
        assert controller.session == original
        with pytest.raises(Exception, match="保存位置"):
            controller.configure(cwd=str(work))
        assert controller.session == original
        snapshot = controller.permission_snapshot()
        from agent_subsystems.workspaces.permissions import authorize_path
        assert authorize_path(snapshot, inner, "read").allowed
        assert not authorize_path(snapshot, inner, "modify").allowed
    finally:
        controller.close()


async def test_script_requires_revision_before_creating_state(tmp_path):
    with pytest.raises(Exception, match="revision"):
        await command(SimpleNamespace(operation="enable", revision=None, json=True), tmp_path / "home")
    assert not (tmp_path / "home").exists()


async def test_check_explains_files_and_uncreated_children(tmp_path, capsys):
    work, home, _ = configured(tmp_path)
    target = work / "new.txt"
    args = SimpleNamespace(operation="check", path=str(target), path_operation="modify", json=True)
    assert await command(args, home) == 0
    target.write_text("file", encoding="utf-8")
    args.path_operation = "read"
    assert await command(args, home) == 0
    import json
    assert all(json.loads(line)["allowed"] for line in capsys.readouterr().out.splitlines())


def test_disabling_adopted_policy_does_not_default_to_full_user(tmp_path):
    work, home, _ = configured(tmp_path)
    store = SQLiteStore(home / "state.sqlite3")
    authority = SQLitePermissions(store)
    identifier, _, _ = authority.begin(replace(authority.load(), enabled=False), 1)
    authority.transition(identifier, "retiring")
    authority.commit(identifier)
    store.close()
    controller = SessionController(home, work, no_trust)
    try:
        controller.default_mode()
        assert controller.session["execution_mode"] == "windows_lpac"
        with pytest.raises(Exception):
            controller.configure()
        controller.configure(execution_options={"sandbox": "current-user"})
        assert controller.session["execution_mode"] == "current_user"
    finally:
        controller.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows source copy")
def test_source_hardlink_becomes_independent_dependency(tmp_path):
    from agent_adapters.local.sandbox_components import copy_file
    source = tmp_path / "installed.dll"
    source.write_bytes(b"installed contents")
    os.link(source, tmp_path / "installed-alias.dll")
    copied = tmp_path / "copy" / "isolated.dll"
    copy_file(source, copied)
    assert copied.read_bytes() == source.read_bytes()
    assert copied.stat().st_nlink == 1
    assert copied.stat().st_ino != source.stat().st_ino
    with pytest.raises(FileExistsError):
        copy_file(source, copied)
