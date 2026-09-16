"""Two real processes/pipe/SQLite/NTFS preparations; model/Run jobs tested separately."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
import subprocess
import sys

import pytest

from agent_adapters.local.dependencies import DependencyManifest
from agent_adapters.storage.permissions import SQLitePermissions, PermissionBusyError
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.permission_host import remote_control, recover_preparations
from agent_contracts.permissions import PathPermission
from agent_subsystems.workspaces.permission_coordination import change_policy

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native multi-host coordination")


@pytest.fixture
def environment(tmp_path):
    work, tools = tmp_path / "Work", tmp_path / "Tools"
    work.mkdir()
    tools.mkdir()
    (work / "fixture.txt").write_text("fixture", encoding="utf-8")
    (tools / "stub.txt").write_text("manifest", encoding="utf-8")
    store = SQLiteStore(tmp_path / "home" / "state.sqlite3")
    authority = SQLitePermissions(store)
    policy = replace(authority.load(), enabled=True, grants=(PathPermission(work, "modify"),))
    identifier, policy, _ = authority.begin(policy, 0)
    authority.transition(identifier, "retiring")
    authority.commit(identifier)
    yield store, authority, work, tools
    store.close()


def start(store, tools):
    return subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("permission_host_child.py")),
         str(store.path), str(tools)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8",
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
    )


async def receive(child):
    line = await asyncio.wait_for(asyncio.to_thread(child.stdout.readline), 15)
    assert line, child.stderr.read() if child.poll() is not None else "Missing child response"
    return json.loads(line)


async def send(child, command):
    child.stdin.write(command + "\n")
    child.stdin.flush()
    return await receive(child)


async def close(child):
    if child.poll() is None:
        child.stdin.write("stop\n")
        child.stdin.flush()
    _, error = await asyncio.wait_for(asyncio.to_thread(child.communicate), 20)
    assert child.returncode == 0, error


async def test_idle_remote_cleanup_keeps_input_process_open(environment):
    store, authority, work, tools = environment
    children = [start(store, tools)]
    try:
        await receive(children[0])
        children.append(start(store, tools))
        await receive(children[1])
        policy = authority.load()
        result = await change_policy(
            authority, replace(policy, grants=(PathPermission(work, "read"),)), 1,
            lambda *args: remote_control(authority, *args),
        )
        assert result.revision == 2 and not authority.preparations()
        for child in children:
            assert child.poll() is None
            assert await send(child, "inspect") == {"running": False, "instances": 0, "revision": 2}
    finally:
        for child in children:
            await close(child)


async def test_busy_remote_preserves_policy_then_idle_retry_succeeds(environment):
    store, authority, work, tools = environment
    child = start(store, tools)
    try:
        await receive(child)
        await send(child, "busy")
        policy = authority.load()
        target = replace(policy, grants=(PathPermission(work, "read"),))
        with pytest.raises(Exception, match="fixture-run"):
            await change_policy(authority, target, 1, lambda *args: remote_control(authority, *args))
        assert authority.load() == policy and authority.pending() is None
        await send(child, "idle")
        await change_policy(authority, target, 1, lambda *args: remote_control(authority, *args))
        assert not authority.preparations()
    finally:
        await close(child)


async def test_dead_host_requires_explicit_repair_and_live_host_is_not_repaired(environment):
    store, authority, _, tools = environment
    child = start(store, tools)
    try:
        await receive(child)
        manifest = DependencyManifest.capture(tools)
        with pytest.raises(PermissionBusyError):
            recover_preparations(store, manifest)
        child.stdin.write("crash\n")
        child.stdin.flush()
        await asyncio.to_thread(child.wait, 10)
        assert child.returncode == 41
        assert len(recover_preparations(store, manifest)) == 1
        assert not authority.preparations()
    finally:
        if child.poll() is None:
            await close(child)
        else:
            child.communicate()


async def test_unaffected_active_narrow_scope_does_not_block_idle_policy_cleanup(environment):
    store, authority, work, tools = environment
    child = start(store, tools)
    try:
        await receive(child)
        await send(child, "busy_read")
        policy = authority.load()
        await change_policy(authority, replace(policy, grants=(PathPermission(work, "read"),)), 1,
                            lambda *args: remote_control(authority, *args))
        assert await send(child, "inspect") == {"running": True, "instances": 1, "revision": 2}
        assert len(authority.preparations()) == 1
        await send(child, "idle")
    finally:
        await close(child)
