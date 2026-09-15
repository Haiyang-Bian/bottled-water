"""Standard-library-only adversarial probes; executes exclusively inside LPAC."""

import argparse
import ctypes
from ctypes import wintypes as w
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time


def attempt(action):
    try:
        return {"allowed": True, "value": action()}
    except OSError as exc:
        return {
            "allowed": False,
            "winerror": getattr(exc, "winerror", None),
            "errno": exc.errno,
            "error": str(exc),
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--own", type=Path, required=True)
    parser.add_argument("--other", type=Path)
    parser.add_argument("--host", type=int)
    parser.add_argument("--handle", type=int)
    parser.add_argument("--sleep", action="store_true")
    parser.add_argument("--leaf", action="store_true")
    parser.add_argument("--network", type=int, nargs=2)
    parser.add_argument("--paths", action="store_true")
    parser.add_argument("--check-symlink", action="store_true")
    args = parser.parse_args()
    if args.paths:
        data = {
            "junction": attempt(lambda: (args.own / "junction-to-C/sample.txt").read_text()),
            "hardlink": attempt(lambda: (args.own / "hardlink-to-C.txt").read_text()),
            "replaced": attempt(lambda: (args.own / "replaced-directory/sample.txt").read_text()),
            "create_hardlink": attempt(
                lambda: os.link(args.other / "sample.txt", args.own / "child-hardlink")
            ),
        }
        if args.check_symlink:
            data["symlink"] = attempt(lambda: (args.own / "symlink-to-C/sample.txt").read_text())
        print(json.dumps(data))
        return
    if args.sleep:
        child = (
            subprocess.Popen(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    __file__,
                    "--own",
                    str(args.own),
                    "--sleep",
                    "--leaf",
                ]
            )
            if not args.leaf
            else None
        )
        if child is not None:
            (args.own / "pids.json").write_text(json.dumps([os.getpid(), child.pid]))
        time.sleep(120)
        return
    if args.network:
        data = {}
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
                        return sock.sendto(b"lpac-lifecycle-probe", (host, port))

                data[f"{label}_{protocol}"] = attempt(connect)
        if not args.leaf:
            child = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-B",
                    __file__,
                    "--own",
                    str(args.own),
                    "--network",
                    *(str(p) for p in args.network),
                    "--leaf",
                ],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
            data["child"] = {"exit_code": child.returncode, "stdout": child.stdout}
        print(json.dumps(data))
        return
    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = dll.OpenProcess
    open_process.restype, open_process.argtypes = w.HANDLE, [w.DWORD, w.BOOL, w.DWORD]
    close = dll.CloseHandle
    close.restype, close.argtypes = w.BOOL, [w.HANDLE]
    started = time.monotonic()
    data = {
        "read_own": attempt(lambda: (args.own / "sample.txt").read_text()),
        "write_own": attempt(lambda: (args.own / "written.txt").write_text("owned")),
        "read_other": attempt(lambda: (args.other / "sample.txt").read_text()),
        "write_other": attempt(lambda: (args.other / "written.txt").write_text("wrong")),
        "private_env_present": "AGENTHUB_PROBE_PRIVATE" in os.environ,
    }
    # Make overlap observable: both differently scoped LPAC processes stay alive.
    (args.own / "ready.txt").write_text(str(os.getpid()))
    time.sleep(0.5)
    for name, access in (
        ("host_read_memory", 0x10),
        ("host_duplicate_handle", 0x40),
        ("host_create_thread", 0x2),
        ("host_terminate", 0x1),
    ):
        handle = open_process(access, False, args.host)
        data[name] = {
            "allowed": bool(handle),
            "winerror": ctypes.get_last_error() if not handle else 0,
        }
        if handle:
            close(handle)
    query = dll.GetFinalPathNameByHandleW
    query.restype, query.argtypes = w.DWORD, [w.HANDLE, w.LPWSTR, w.DWORD, w.DWORD]
    buffer = ctypes.create_unicode_buffer(32768)
    try:
        size = query(args.handle, buffer, len(buffer), 2)
        data["unlisted_handle"] = {
            "query_succeeded": bool(size),
            "path": buffer.value if size else None,
            "winerror": ctypes.get_last_error() if not size else 0,
        }
    except OSError as exc:
        if exc.winerror not in (6, -1073741816):
            raise
        data["unlisted_handle"] = {
            "query_succeeded": False,
            "path": None,
            "winerror": exc.winerror,
        }
    data["window"] = [started, time.monotonic()]
    print(json.dumps(data))


if __name__ == "__main__":
    main()
