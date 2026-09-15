"""Fixed NT namespace dependency experiment; never grants business file access.

This is an administrator-side P1 probe, not a production setup/repair service.
Every added ACE is non-inheriting and belongs to one fresh capability. Removal
edits the current ACL, not an old saved DACL. No ownership or privilege changes.
"""

import ctypes
from ctypes import wintypes as w
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Target:
    path: str
    kind: str
    mask: int


TARGETS = (
    Target("\\GLOBAL??", "Directory", 0x20003),
    Target("\\GLOBAL??\\C:", "SymbolicLink", 0x20001),
    Target("\\GLOBAL??\\D:", "SymbolicLink", 0x20001),
    Target("\\GLOBAL??\\MountPointManager", "SymbolicLink", 0x20001),
    Target("\\\\.\\MountPointManager", "Device", 0x120089),
)


def capability_name(experiment):
    if not re.fullmatch(r"[0-9a-f]{32}", experiment):
        raise ValueError("Expected a fresh lowercase UUID hex experiment identifier")
    return "AgentHub.Probe.Namespace." + experiment


class UnicodeString(ctypes.Structure):
    _fields_ = [("Length", w.WORD), ("MaximumLength", w.WORD), ("Buffer", w.LPWSTR)]


class ObjectAttributes(ctypes.Structure):
    _fields_ = [
        ("Length", w.ULONG),
        ("RootDirectory", w.HANDLE),
        ("ObjectName", ctypes.POINTER(UnicodeString)),
        ("Attributes", w.ULONG),
        ("SecurityDescriptor", w.LPVOID),
        ("SecurityQualityOfService", w.LPVOID),
    ]


def open_target(target):
    import win32file

    if target not in TARGETS:
        raise ValueError("Object is outside the fixed namespace dependency list")
    if target.kind == "Device":
        return win32file.CreateFile(target.path, 0x60000, 7, None, 3, 0, None)
    text = ctypes.create_unicode_buffer(target.path)
    string = UnicodeString(len(target.path) * 2, len(text) * 2, ctypes.cast(text, w.LPWSTR))
    attr = ObjectAttributes(
        ctypes.sizeof(ObjectAttributes), None, ctypes.pointer(string), 0x40, None, None
    )
    handle = w.HANDLE()
    op = getattr(ctypes.WinDLL("ntdll"), "NtOpen" + target.kind + "Object")
    op.restype = ctypes.c_long
    op.argtypes = [ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.POINTER(ObjectAttributes)]
    status = op(ctypes.byref(handle), 0x60000, ctypes.byref(attr))
    if status < 0:
        raise OSError(f"{target.path}: NTSTATUS 0x{status & 0xFFFFFFFF:08x}")
    import pywintypes

    return pywintypes.HANDLE(handle.value)


def read_acl(handle):
    import win32security as security

    acl = security.GetSecurityInfo(
        int(handle), security.SE_KERNEL_OBJECT, security.DACL_SECURITY_INFORMATION
    ).GetSecurityDescriptorDacl()
    if acl is None:
        raise RuntimeError("Refuse to modify a NULL DACL")
    return acl


def entries_for_sid(acl, sid):
    import win32security as security

    return [
        (i, acl.GetAce(i))
        for i in range(acl.GetAceCount())
        if security.ConvertSidToStringSid(acl.GetAce(i)[-1]) == sid
    ]


def remove_owned_ace(acl, sid, mask):
    """Reject changed/ambiguous entries; preserve all unrelated ACEs and ordering."""
    found = entries_for_sid(acl, sid)
    if len(found) > 1 or any(ace[:2] != ((0, 0), mask) for _, ace in found):
        raise RuntimeError("Managed namespace ACE changed; manual inspection required")
    for index, _ in found:
        acl.DeleteAce(index)
    return bool(found)


def write_acl(handle, acl):
    import win32security as security

    security.SetSecurityInfo(
        int(handle),
        security.SE_KERNEL_OBJECT,
        security.DACL_SECURITY_INFORMATION,
        None,
        None,
        acl,
        None,
    )


def describe_acl(acl):
    import win32security as security

    sd = security.SECURITY_DESCRIPTOR()
    sd.SetSecurityDescriptorDacl(True, acl, False)
    return security.ConvertSecurityDescriptorToStringSecurityDescriptor(
        sd, 1, security.DACL_SECURITY_INFORMATION
    )
