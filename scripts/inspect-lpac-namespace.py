"""Read-only NT object DACL diagnostic for GetFinalPathNameByHandle failures."""

import ctypes
from ctypes import wintypes as w
import json

import win32security


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


def main():
    ntdll = ctypes.WinDLL("ntdll")
    close = ntdll.NtClose
    close.restype, close.argtypes = ctypes.c_long, [w.HANDLE]
    for name, kind in (
        ("\\GLOBAL??", "Directory"),
        ("\\GLOBAL??\\D:", "SymbolicLink"),
        ("\\GLOBAL??\\MountPointManager", "SymbolicLink"),
    ):
        text = ctypes.create_unicode_buffer(name)
        string = UnicodeString(len(name) * 2, len(text) * 2, ctypes.cast(text, w.LPWSTR))
        attr = ObjectAttributes(
            ctypes.sizeof(ObjectAttributes), None, ctypes.pointer(string), 0x40, None, None
        )
        handle = w.HANDLE()
        op = getattr(ntdll, "NtOpen" + kind + "Object")
        op.restype = ctypes.c_long
        op.argtypes = [ctypes.POINTER(w.HANDLE), w.DWORD, ctypes.POINTER(ObjectAttributes)]
        status = op(ctypes.byref(handle), 0x20000, ctypes.byref(attr))
        result = {"path": name, "status": hex(status & 0xFFFFFFFF)}
        if status >= 0:
            try:
                sd = win32security.GetSecurityInfo(
                    handle.value,
                    win32security.SE_KERNEL_OBJECT,
                    win32security.DACL_SECURITY_INFORMATION,
                )
                result["dacl"] = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
                    sd,
                    1,
                    win32security.DACL_SECURITY_INFORMATION,
                )
            except OSError as exc:
                result["error"] = str(exc)
            finally:
                close(handle)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
