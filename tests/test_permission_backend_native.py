"""Formal preparation backend on owned NTFS fixtures, without model or elevation."""

import json
import os
from dataclasses import replace

import pytest

from agent_adapters.local.dependencies import DependencyManifest
from agent_adapters.local.windows_permission_backend import (
    WindowsPermissionBackend, object_handle, owned_aces,
)
from agent_adapters.storage.permissions import SQLitePermissions
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.permissions import PathPermission
from agent_subsystems.workspaces.permission_preparation import PreparedPolicy
from agent_subsystems.workspaces.permissions import freeze_policy

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Native NTFS ACL fixture")


def fixture(tmp_path):
    work, archive, dependencies = (tmp_path / name for name in ("Work", "Archive", "Tools"))
    for path in (work, archive, dependencies):
        path.mkdir()
        (path / "sample.txt").write_text("owned fixture", encoding="utf-8")
    store = SQLiteStore(tmp_path / "state" / "state.sqlite3")
    authority = SQLitePermissions(store)
    policy = replace(authority.load(), enabled=True,
                     grants=(PathPermission(work, "modify"), PathPermission(archive, "read")))
    transition, policy, _ = authority.begin(policy, 0)
    authority.transition(transition, "retiring")
    authority.commit(transition)
    authority.host("fixture", {})
    backend = WindowsPermissionBackend(authority, "fixture", DependencyManifest.capture(dependencies))
    return store, authority, backend, PreparedPolicy(freeze_policy(policy),
                                                    backend.dependencies.digest, backend)


def test_formal_preparation_cleanup_preserves_other_acl(tmp_path):
    import win32security as sec
    store, authority, backend, prepared = fixture(tmp_path)
    work = tmp_path / "Work"
    try:
        prepared.prepare()
        record = authority.preparations()[0]
        body = json.loads(record["body"])
        with object_handle(work, write=True) as handle:
            acl, own = owned_aces(handle, body["sid"])
            assert own
            extra_sid = "S-1-5-21-123456789-987654321-112233445-9999"
            acl.AddAccessAllowedAceEx(4, 0, 0x120089, sec.ConvertStringSidToSid(extra_sid))
            sec.SetSecurityInfo(handle, 1, 4, None, None, acl, None)
        (work / "new.txt").write_text("newly inherited", encoding="utf-8")
        first = prepared.acquire("one")
        first.finish(job_drained=True)
        second = prepared.acquire("two")
        second.finish(job_drained=True)
        prepared.retire()
        assert not authority.preparations()
        for path in (work, work / "sample.txt", work / "new.txt", tmp_path / "Archive"):
            with object_handle(path) as handle:
                assert not owned_aces(handle, body["sid"])[1]
        with object_handle(work) as handle:
            assert owned_aces(handle, extra_sid)[1]
        with pytest.raises(Exception, match="retired"):
            prepared.acquire("after")
    finally:
        prepared.retire()
        store.close()


def test_prepare_interruption_compensates_only_own_acl(tmp_path):
    store, authority, backend, prepared = fixture(tmp_path)
    def interrupt(event):
        if event["phase"] == "preparing":
            raise KeyboardInterrupt()
    backend.progress = interrupt
    try:
        with pytest.raises(KeyboardInterrupt):
            prepared.prepare()
        assert not authority.preparations()
        assert str(prepared.state) == "retired"
    finally:
        prepared.retire()
        store.close()


def test_cleanup_identity_change_remains_pending(tmp_path):
    store, authority, backend, prepared = fixture(tmp_path)
    try:
        prepared.prepare()
        body = backend.records[prepared.generation]["body"]
        original = list(body["roots"][0]["identity"])
        body["roots"][0]["identity"][1] += 1
        with pytest.raises(Exception, match="Root requires repair"):
            prepared.retire()
        assert authority.preparations()[0]["state"] == "repair_required"
        body["roots"][0]["identity"] = original
    finally:
        prepared.retire()
        store.close()


def test_retired_storage_generation_cannot_reopen(tmp_path):
    store, authority, backend, prepared = fixture(tmp_path)
    try:
        prepared.prepare()
        prepared.retire()
        record = backend.records[prepared.generation]
        with pytest.raises(Exception, match="cannot reopen"):
            authority.save_preparation(prepared.generation, "fixture", prepared.snapshot,
                                       record["body"], "prepared")
    finally:
        store.close()
