"""Read-only ETW registration diagnostic for the P1 PowerShell startup failure."""

import ctypes
from ctypes import wintypes as w
import json
from uuid import UUID

import win32security


def main():
    dll = ctypes.WinDLL("advapi32", use_last_error=True)
    query = dll.EventAccessQuery
    query.argtypes = [w.LPVOID, w.LPVOID, ctypes.POINTER(w.DWORD)]
    query.restype = w.ULONG
    register = dll.EventRegister
    register.argtypes = [w.LPVOID, w.LPVOID, w.LPVOID, ctypes.POINTER(ctypes.c_uint64)]
    register.restype = w.ULONG
    unregister = dll.EventUnregister
    unregister.argtypes = [ctypes.c_uint64]
    unregister.restype = w.ULONG
    for name, value in (
        ("PowerShellCore", "f90714a8-5509-434a-bf6d-b1624c8a19a2"),
        ("WindowsPowerShell", "a0c1853b-5c40-4b15-8766-3cf1c58f985a"),
    ):
        guid = ctypes.create_string_buffer(UUID(value).bytes_le)
        size = w.DWORD()
        first = query(guid, None, ctypes.byref(size))
        result = {"name": name, "guid": value, "query_code": first}
        if first == 122 and size.value < 1024 * 1024:
            buffer = ctypes.create_string_buffer(size.value)
            result["query_code"] = query(guid, buffer, ctypes.byref(size))
            if result["query_code"] == 0:
                sd = win32security.SECURITY_DESCRIPTOR(bytes(buffer))
                result["dacl"] = win32security.ConvertSecurityDescriptorToStringSecurityDescriptor(
                    sd,
                    1,
                    win32security.DACL_SECURITY_INFORMATION,
                )
        handle = ctypes.c_uint64()
        result["registration_code"] = register(guid, None, None, ctypes.byref(handle))
        if result["registration_code"] == 0:
            unregister(handle)
        print(json.dumps(result))


if __name__ == "__main__":
    main()
