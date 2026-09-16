"""Fixed protected initializer. No business paths, commands or model input accepted."""

import argparse
import ctypes
import json
import hashlib
import os
from pathlib import Path

from .windows_lpac import system_capability_sids
from .windows_namespace import (
    TARGETS, capability_name, entries_for_sid, open_target, read_acl, remove_owned_ace, write_acl,
)


def check_protected(root):
    import win32security as sec
    root = Path(root)
    expected = Path(os.environ["ProgramFiles"]) / "AgentHub" / "Sandbox"
    if not root.is_relative_to(expected):
        raise RuntimeError("Initializer must be in the protected installation")
    for path in (root, *root.parents):
        if path.lstat().st_file_attributes & 0x400:
            raise RuntimeError("Protected component alias")
        sd = sec.GetNamedSecurityInfo(str(path), 1, 5)
        owner = sec.ConvertSidToStringSid(sd.GetSecurityDescriptorOwner())
        if owner not in {"S-1-5-18", "S-1-5-32-544"}:
            raise RuntimeError("Component owner is not SYSTEM/Administrators")
        acl = sd.GetSecurityDescriptorDacl()
        if acl is None:
            raise RuntimeError("Unprotected component DACL")
        for index in range(acl.GetAceCount()):
            ace = acl.GetAce(index)
            if ace[0][0] == 0 and ace[1] & 0xD0156 and sec.ConvertSidToStringSid(
                ace[-1]
            ) not in {"S-1-5-18", "S-1-5-32-544"}:
                raise RuntimeError("Component directory is writable by an ordinary principal")
        if path == expected.parent:
            break


def verify_component(root, hashes):
    """Verify protected interpreter/code and reject additions before elevation."""
    root = Path(root)
    check_protected(root)
    observed = {}
    stack = [root]
    while stack:
        path = stack.pop()
        info = path.lstat()
        check_protected(path)
        if path.is_dir():
            stack.extend(path.iterdir())
            continue
        if info.st_nlink != 1:
            raise RuntimeError("Protected component hard link")
        relative = str(path.relative_to(root))
        if relative == "initialization.json":
            continue  # Mutable administrator audit, never imported or executed.
        with path.open("rb") as stream:
            observed[relative] = hashlib.file_digest(stream, "sha256").hexdigest()
    if observed != hashes:
        raise RuntimeError("Protected component manifest changed; initialization refused")


def initialize(root, action):
    check_protected(root)
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("Fixed initialization requires explicit administrator approval")
    manifest = json.loads((root / "component.json").read_text(encoding="utf-8"))
    sid = system_capability_sids(capability_name(manifest["namespace"]))[0]
    ledger = root / "initialization.json"
    report = {"action": action, "state": "preparing", "objects": [], "sid": sid}
    def save():
        with ledger.open("w", encoding="utf-8") as stream:
            json.dump(report, stream)
            stream.flush()
            os.fsync(stream.fileno())
    save()
    handles = []
    try:
        for target in TARGETS:
            handles.append((target, open_target(target)))
        for target, handle in handles:
            row = {"path": target.path, "mask": target.mask, "state": "intent"}
            report["objects"].append(row)
            save()
            acl = read_acl(handle)
            own = entries_for_sid(acl, sid)
            if action == "remove":
                if remove_owned_ace(acl, sid, target.mask):
                    write_acl(handle, acl)
                if entries_for_sid(read_acl(handle), sid):
                    raise RuntimeError("Initialization removal unconfirmed")
            else:
                if own and (len(own) != 1 or own[0][1][:2] != ((0, 0), target.mask)):
                    raise RuntimeError("Initialization ACE changed")
                if not own:
                    import win32security as sec
                    acl.AddAccessAllowedAceEx(4, 0, target.mask, sec.ConvertStringSidToSid(sid))
                    write_acl(handle, acl)
                checked = entries_for_sid(read_acl(handle), sid)
                if len(checked) != 1 or checked[0][1][:2] != ((0, 0), target.mask):
                    raise RuntimeError("Initialization ACE unconfirmed")
            row["state"] = "removed" if action == "remove" else "applied"
            save()
        report["state"] = "removed" if action == "remove" else "ready"
        save()
    except BaseException as exc:
        report.update(state="repair_required", error=type(exc).__name__, detail=str(exc))
        save()
        raise
    finally:
        for _, handle in handles:
            handle.Close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["setup", "remove", "check"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[3]
    if args.action == "check":
        for target in TARGETS:
            handle = open_target(target, write=False)
            try:
                read_acl(handle)
            finally:
                handle.Close()
        print("Fixed component dependencies ready; no permissions changed")
        return
    initialize(root, args.action)
