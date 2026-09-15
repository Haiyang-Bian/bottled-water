"""Standard-library native attempts, exclusively against a new probe fixture."""

import argparse
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path


def attempt(action):
    try:
        value = action()
        return {"allowed": True, "value": value if isinstance(value, (str, int, bool)) else None}
    except OSError as exc:
        return {"allowed": False, "errno": exc.errno, "winerror": exc.winerror}


def open_access(path, mask):
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [w.LPCWSTR, w.DWORD, w.DWORD, w.LPVOID, w.DWORD, w.DWORD, w.HANDLE]
    create.restype = w.HANDLE
    handle = create(str(path), mask, 7, None, 3, 0x02000000, None)
    if handle == w.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    close = kernel.CloseHandle
    close.argtypes, close.restype = [w.HANDLE], w.BOOL
    close(handle)
    return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--scratch", required=True)
    parser.add_argument("--other-scratch", required=True)
    parser.add_argument("--narrow", action="store_true")
    args = parser.parse_args()
    root = Path(args.root)
    archive = root / "Group/Archive"
    private = root / "Private"
    result = {}

    def record(name, action):
        result[name] = attempt(action)

    record("read_work", lambda: (root / "Work/sample.txt").read_text())
    record("write_work", lambda: (root / "Work/sample.txt").write_text("changed"))
    record("read_archive", lambda: (archive / "sample.txt").read_text())
    record("write_archive", lambda: (archive / "sample.txt").write_text("BAD"))
    record("read_private", lambda: (private / "sample.txt").read_text())
    record("write_private", lambda: (private / "sample.txt").write_text("BAD"))
    record("read_own_scratch", lambda: (Path(args.scratch) / "private.txt").read_text())
    record("read_other_scratch", lambda: (Path(args.other_scratch) / "private.txt").read_text())
    for label, path in (("archive", archive / "sample.txt"),
                        ("work", root / "Work/sample.txt"), ("anchor", root / "Group")):
        record("write_dac_" + label, lambda p=path: open_access(p, 0x40000))
        record("write_owner_" + label, lambda p=path: open_access(p, 0x80000))
    record("delete_child_group", lambda: open_access(root / "Group", 0x40))
    record("create_archive", lambda: (archive / "new.txt").write_text("BAD"))
    record("delete_archive_file", lambda: (archive / "delete.txt").unlink())
    record("rename_archive", lambda: archive.rename(root / "Archive-moved"))
    record("rename_ancestor", lambda: (root / "Group").rename(root / "Group-moved"))
    record("create_work", lambda: (root / "Work/new.txt").write_text("created"))
    record("rename_work_file", lambda: (root / "Work/rename.txt").rename(root / "Work/renamed.txt"))
    record("delete_work_file", lambda: (root / "Work/delete.txt").unlink())
    # Creating a file must not grant security-management authority via ownership.
    if result["create_work"]["allowed"]:
        record("write_dac_new", lambda: open_access(root / "Work/new.txt", 0x40000))
        record("write_owner_new", lambda: open_access(root / "Work/new.txt", 0x80000))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
