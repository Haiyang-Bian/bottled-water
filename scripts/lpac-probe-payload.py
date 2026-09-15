"""Unprivileged stdlib-only payload for the native LPAC experiment."""

import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import socket
import subprocess
import sys


def attempt(action):
    try:
        value = action()
        return {"allowed": True, "value": str(value)}
    except OSError as exc:
        return {
            "allowed": False,
            "winerror": getattr(exc, "winerror", None),
            "errno": exc.errno,
            "error": str(exc),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--child", action="store_true")
    parser.add_argument("--network", type=int, nargs=2)
    parser.add_argument("--cwd-diagnostic", action="store_true")
    args = parser.parse_args()
    root = args.fixture
    if args.cwd_diagnostic:
        dll = ctypes.WinDLL("kernel32", use_last_error=True)
        create = dll.CreateFileW
        create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
        create.restype = w.HANDLE
        query = dll.GetFinalPathNameByHandleW
        query.argtypes = [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
        query.restype = w.DWORD
        long_name = dll.GetLongPathNameW
        long_name.argtypes = [w.LPCWSTR, w.LPWSTR, w.DWORD]
        long_name.restype = w.DWORD
        get_attributes = dll.GetFileAttributesW
        get_attributes.argtypes = [w.LPCWSTR]
        get_attributes.restype = w.DWORD
        close = dll.CloseHandle
        close.argtypes = [w.HANDLE]
        close.restype = w.BOOL
        result = {"os_getcwd": attempt(os.getcwd)}
        cwd = str(root / "B")
        handle = create(cwd, 0, 7, None, 3, 0x02000000, None)
        if handle == w.HANDLE(-1).value:
            result["create_error"] = ctypes.get_last_error()
        else:
            try:
                for flags in (0, 1, 2, 4, 8):
                    buffer = ctypes.create_unicode_buffer(32768)
                    length = query(handle, buffer, len(buffer), flags)
                    result[f"final_path_{flags}"] = {
                        "value": buffer.value if length else None,
                        "error": ctypes.get_last_error() if not length else 0,
                    }
            finally:
                close(handle)
        buffer = ctypes.create_unicode_buffer(32768)
        length = long_name(cwd, buffer, len(buffer))
        result["long_name"] = {
            "value": buffer.value if length else None,
            "error": ctypes.get_last_error() if not length else 0,
        }
        result["ancestor_attributes"] = {}
        for path in (Path(cwd), *Path(cwd).parents):
            attrs = get_attributes(str(path))
            result["ancestor_attributes"][str(path)] = {
                "attributes": attrs,
                "error": ctypes.get_last_error() if attrs == 0xFFFFFFFF else 0,
            }
        print(json.dumps(result))
        return
    if args.network:
        results = {}
        for family, host, port, label in (
            (socket.AF_INET, "127.0.0.1", args.network[0], "ipv4"),
            (socket.AF_INET6, "::1", args.network[1], "ipv6"),
        ):
            for kind, protocol in ((socket.SOCK_STREAM, "tcp"), (socket.SOCK_DGRAM, "udp")):

                def connect(family=family, host=host, port=port, kind=kind):
                    with socket.socket(family, kind) as sock:
                        sock.settimeout(1)
                        if kind == socket.SOCK_STREAM:
                            sock.connect((host, port))
                            return "connected"
                        return sock.sendto(b"agenthub-lpac-fixture", (host, port))

                results[f"{label}_{protocol}"] = attempt(connect)
        print(json.dumps(results))
        return
    results = {
        "read_A": attempt(lambda: (root / "A/sample.txt").read_text()),
        "write_A": attempt(lambda: (root / "A/sample.txt").write_text("changed")),
        "write_B": attempt(lambda: (root / "B/python.txt").write_text("python-fixture")),
        "read_C": attempt(lambda: (root / "C/sample.txt").read_text()),
        "read_host_state": attempt(lambda: (root.parent / "probe.json").read_bytes()),
        "write_runtime": attempt(lambda: Path(__file__).write_text("changed")),
    }
    if not args.child:
        process = subprocess.run(
            [sys.executable, "-I", "-S", "-B", __file__, "--fixture", str(root), "--child"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        results["child"] = {
            "exit_code": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
        }
    print(json.dumps(results))


if __name__ == "__main__":
    main()
