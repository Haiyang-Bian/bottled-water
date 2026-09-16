"""Read-only NT object DACL diagnostic for GetFinalPathNameByHandle failures."""

import ctypes
from ctypes import wintypes as w
import argparse
import json
from pathlib import Path

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
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-access-probe", action="store_true",
                        help="Only try opening WRITE_DAC handles; never change an ACL")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve() if args.output else None
    if output is not None and (output.parent != Path(__file__).resolve().parents[1] / "var"
                               or output.exists()):
        parser.error("Output must be a new file directly in repository var")
    report = []
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
        access = 0x60000 if args.write_access_probe else 0x20000
        status = op(ctypes.byref(handle), access, ctypes.byref(attr))
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
        report.append(result)
    if output is not None:
        output.write_text(json.dumps({"elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
                                      "requested_write_dac": args.write_access_probe,
                                      "acl_mutations": 0, "results": report}, indent=2),
                          encoding="utf-8")


if __name__ == "__main__":
    main()
