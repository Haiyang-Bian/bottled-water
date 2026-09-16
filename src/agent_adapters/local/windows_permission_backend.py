"""Ordinary-user NTFS preparation with durable intents and owned-ACE cleanup.

No inheritance barriers, administrator privileges or complete DACL restoration.
Only user-selected roots are traversed. Unknown aliases/identities fail closed.
"""

import os
from contextlib import contextmanager
from pathlib import Path

from agent_contracts.errors import OperationError
from agent_subsystems.workspaces.permissions import inheritable_roots
from .windows_lpac import system_capability_sids

READ = 0x1200A9
MODIFY = 0x1301BF  # Never WRITE_DAC, WRITE_OWNER or FILE_DELETE_CHILD.


def canonical_ntfs(path):
    if os.name != "nt":
        raise OperationError("unsupported_platform", "Windows 11 NTFS is required")
    import win32api
    import win32file

    path = Path(os.path.abspath(path))
    if str(path).startswith("\\\\") or not path.drive or ":" in str(path)[2:]:
        raise OperationError("unsupported_path", "Only local NTFS directories are supported")
    for part in (path, *path.parents):
        if part.lstat().st_file_attributes & 0x400:
            raise OperationError("unsupported_alias", "目录不能包含 reparse point: " + str(part))
    volume = win32file.GetVolumePathName(str(path))
    if win32file.GetDriveType(volume) != 3 or win32api.GetVolumeInformation(volume)[4] != "NTFS":
        raise OperationError("unsupported_filesystem", "Only local fixed NTFS is supported")
    if not path.is_dir():
        raise OperationError("invalid_directory", str(path))
    return Path(os.path.normcase(str(path)))


def extended(path):
    return str(path) if str(path).startswith("\\\\?\\") else "\\\\?\\" + str(path)


def object_identity(handle):
    import win32file
    info = win32file.GetFileInformationByHandle(handle)
    return [info[4], (info[8] << 32) | info[9]]


@contextmanager
def object_handle(path, *, write=False, pin=False):
    import win32file
    info = os.lstat(extended(path))
    if info.st_file_attributes & 0x400 or not Path(path).is_dir() and info.st_nlink != 1:
        raise OperationError("unsupported_alias", "Unsupported alias: " + str(path))
    handle = win32file.CreateFile(
        extended(path), 0x20000 | (0x40000 if write else 0), 3 if pin else 7,
        None, 3, 0x02200000, None,
    )
    try:
        final = win32file.GetFinalPathNameByHandle(handle, 0)
        if os.path.normcase(final.removeprefix("\\\\?\\")) != os.path.normcase(str(path)):
            raise OperationError("permission_object_changed", "Final object path changed")
        native = win32file.GetFileInformationByHandle(handle)
        if native[0] & 0x400 or not native[0] & 0x10 and native[7] != 1:
            raise OperationError("unsupported_alias", "Object alias changed while opening")
        yield handle
    finally:
        handle.Close()


def owned_aces(handle, sid):
    import win32security as sec
    acl = sec.GetSecurityInfo(handle, sec.SE_FILE_OBJECT, 4).GetSecurityDescriptorDacl()
    if acl is None:
        raise OperationError("unsupported_acl", "NULL DACL is not supported")
    values = [acl.GetAce(i) for i in range(acl.GetAceCount())]
    if any(ace[0][0] not in (0, 1) for ace in values):
        raise OperationError("unsupported_acl", "Unsupported object ACE")
    own = [ace for ace in values if sec.ConvertSidToStringSid(ace[-1]) == sid]
    return acl, own


def edit_owned(handle, sid, entries=()):
    import win32security as sec
    acl, own = owned_aces(handle, sid)
    previous = [acl.GetAce(i) for i in range(acl.GetAceCount())
                if entries or sec.ConvertSidToStringSid(acl.GetAce(i)[-1]) != sid]
    added = [((0, flags), mask, sec.ConvertStringSidToSid(sid)) for flags, mask in entries]
    explicit = [ace for ace in previous if not ace[0][1] & 16] + added
    explicit.sort(key=lambda ace: 0 if ace[0][0] == 1 else 1)
    updated = sec.ACL()
    for ace in explicit + [ace for ace in previous if ace[0][1] & 16]:
        method = updated.AddAccessDeniedAceEx if ace[0][0] == 1 else updated.AddAccessAllowedAceEx
        method(4, ace[0][1], ace[1], ace[-1])
    sec.SetSecurityInfo(handle, sec.SE_FILE_OBJECT, 4, None, None, updated, None)
    _, checked = owned_aces(handle, sid)
    if bool(checked) != bool(entries):
        raise OperationError("permission_acl_unconfirmed", "Owned ACE update is unconfirmed")


def inventory(root, check):
    stack = [root]
    while stack:
        check()
        path = stack.pop()
        with object_handle(path) as handle:
            item = {"path": str(path), "identity": object_identity(handle),
                    "directory": path.is_dir()}
        yield item
        if item["directory"]:
            stack.extend(reversed(sorted(Path(entry.path.removeprefix("\\\\?\\"))
                                         for entry in os.scandir(extended(path)))))


class WindowsPermissionBackend:
    def __init__(self, authority, host, dependencies, *, progress=None, cancelled=None):
        self.authority, self.host, self.dependencies = authority, host, dependencies
        self.progress = progress or (lambda _: None)
        self.cancelled = cancelled or (lambda: False)
        self.records, self.pins = {}, {}

    def check_cancel(self):
        if self.cancelled():
            raise OperationError("permission_prepare_cancelled", "权限准备已取消")

    def save(self, generation):
        record = self.records[generation]
        self.authority.save_preparation(generation, self.host, record["snapshot"],
                                        record["body"], record["state"])

    def prepare(self, generation, snapshot):
        from contextlib import ExitStack
        roots = inheritable_roots(snapshot)
        pins = self.pins[generation] = ExitStack()
        body = {"sid": system_capability_sids("AgentHub.Probe.Policy." + generation)[0],
                "roots": [], "objects": [], "intents": [],
                "dependency_digest": self.dependencies.digest}
        self.records[generation] = {"snapshot": snapshot, "body": body, "state": "preparing"}
        self.save(generation)  # First intent precedes every mutation.
        for rule in roots:
            path = canonical_ntfs(rule.path)
            handle = pins.enter_context(object_handle(path, write=True, pin=True))
            body["roots"].append({"path": str(path), "identity": object_identity(handle),
                                  "access": rule.access.value})
            for item in inventory(path, self.check_cancel):
                body["objects"].append(item)
                self.progress({"phase": "inspecting", "path": item["path"],
                               "objects": len(body["objects"])})
        self.save(generation)
        for rule in roots:
            objects = [item for item in body["objects"]
                       if Path(item["path"]).is_relative_to(rule.path)]
            for item in objects:
                self.check_cancel()
                path = Path(item["path"])
                with object_handle(path, write=True) as handle:
                    if object_identity(handle) != item["identity"]:
                        raise OperationError("permission_object_changed", str(path))
                    _, own = owned_aces(handle, body["sid"])
                    desired = MODIFY if rule.access == "modify" else READ
                    available = 0
                    for ace in own:
                        if ace[0][0] == 0 and not ace[0][1] & 8:
                            available |= ace[1]
                    if available & desired == desired:
                        continue
                    if path == rule.path and rule.access == "modify":
                        entries = [(0, MODIFY & ~0x10000), (11, MODIFY)]
                    else:
                        entries = [(3 if item["directory"] else 0,
                                    MODIFY if rule.access == "modify" else READ)]
                    intent = {**item, "entries": entries, "state": "intent"}
                    body["intents"].append(intent)
                    self.save(generation)
                    edit_owned(handle, body["sid"], entries)
                    intent["state"] = "applied"
                    self.save(generation)
                self.progress({"phase": "preparing", "path": str(path),
                               "objects": len(body["objects"])})
        self.records[generation]["state"] = "prepared"
        self.save(generation)

    def verify(self, generation):
        record = self.records[generation]
        if record["state"] != "prepared":
            raise OperationError("permission_preparation_invalid", "Preparation is not ready")
        self.dependencies.verify()
        for item in record["body"]["roots"]:
            with object_handle(Path(item["path"])) as handle:
                _, own = owned_aces(handle, record["body"]["sid"])
                rights = [(ace[0][0], ace[0][1] & ~16, ace[1]) for ace in own]
                expected = ([(0, 0, MODIFY & ~0x10000), (0, 11, MODIFY)]
                            if item["access"] == "modify" else [(0, 3, READ)])
                if object_identity(handle) != item["identity"] or any(
                    entry not in rights for entry in expected
                ):
                    raise OperationError("permission_object_changed", "Prepared root changed")

    def retire(self, generation):
        record = self.records.get(generation)
        if record is None or record["state"] == "retired":
            return
        record["state"] = "retiring"
        self.save(generation)
        try:
            body = record["body"]
            # Known objects cannot silently disappear/reappear at another path.
            for item in body["roots"]:
                with object_handle(Path(item["path"])) as handle:
                    if object_identity(handle) != item["identity"]:
                        raise OperationError("permission_object_changed", "Root requires repair")
            for item in body["intents"]:
                if not Path(item["path"]).exists():
                    raise OperationError("permission_object_changed", "Granted object missing")
                with object_handle(Path(item["path"])) as handle:
                    if object_identity(handle) != item["identity"]:
                        raise OperationError("permission_object_changed", "Granted object replaced")
            for root in body["roots"]:
                for item in inventory(Path(root["path"]), lambda: None):
                    with object_handle(Path(item["path"]), write=True) as handle:
                        if owned_aces(handle, body["sid"])[1]:
                            edit_owned(handle, body["sid"])
            record["state"] = "retired"
            self.save(generation)
        except BaseException:
            record["state"] = "repair_required"
            self.save(generation)
            raise
        finally:
            if generation in self.pins:
                self.pins.pop(generation).close()
