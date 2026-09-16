"""Extra P1 fault and alias probes against caller-owned isolated fixtures."""

import ctypes
from ctypes import wintypes as w
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import patch


def startup_faults(profile, launch, report, save):
    import win32api
    import win32event
    import win32job

    actual_assign = win32job.AssignProcessToJobObject
    for fault in ("assign", "token"):
        retained = []

        def assign(job, process):
            # Keep a reference acquired before failure, rather than re-open a PID.
            duplicate = win32api.DuplicateHandle(
                win32api.GetCurrentProcess(),
                process,
                win32api.GetCurrentProcess(),
                0x100000,
                False,
                0,
            )
            retained.append(duplicate)
            if fault == "assign":
                raise RuntimeError("Injected Job assignment failure")
            actual_assign(job, process)

        error = None
        try:
            with patch("win32job.AssignProcessToJobObject", side_effect=assign):
                if fault == "token":
                    with patch("lpac_probe.native.token_flag", return_value=False):
                        launch(0, "--sleep")
                else:
                    launch(0, "--sleep")
        except RuntimeError as exc:
            error = str(exc)
        finally:
            dead = bool(retained) and all(
                win32event.WaitForSingleObject(h, 5000) == 0 for h in retained
            )
            for handle in retained:
                handle.Close()
        if not dead:
            profile.cleanup_blocked = True
        report["results"]["startup_" + fault] = {"error": error, "process_dead": dead}
        expected = (
            "Injected Job assignment failure"
            if fault == "assign"
            else "Created process does not have the required LPAC token"
        )
        report["checks"]["startup_" + fault] = (
            dead and error == expected and not profile.cleanup_blocked
        )
        save()


def aliases(output, launch, report, save, *, provided_symbolic=None):
    import _winapi

    own, private = output / "A", output / "C"
    junction = own / "junction-to-C"
    symlink = own / "symlink-to-C"
    hardlink = own / "hardlink-to-C.txt"
    replacement = own / "replaced-directory"
    source = Path(__file__).resolve().parents[1] / "probe-windows-lpac.py"
    spec = importlib.util.spec_from_file_location("lpac_fixture_guard", source)
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)

    def rejected(path):
        try:
            list(probe.fixture_tree(path))
        except RuntimeError:
            return True
        return False

    # Test real host-created aliases without ever granting ACLs through them.
    try:
        _winapi.CreateJunction(str(private), str(junction))
        os.link(private / "sample.txt", hardlink)
        report["checks"]["reject_alias_grants"] = rejected(junction) and rejected(hardlink)
        (replacement).mkdir()
        (replacement / "sample.txt").write_text("own-before-replacement")
        (replacement / "sample.txt").unlink()
        replacement.rmdir()  # Own empty directory; no recursive deletion.
        _winapi.CreateJunction(str(private), str(replacement))
        create_link = ctypes.WinDLL("kernel32", use_last_error=True).CreateSymbolicLinkW
        create_link.argtypes, create_link.restype = [w.LPCWSTR, w.LPCWSTR, w.DWORD], w.BOOLEAN
        if provided_symbolic is not None:
            if not provided_symbolic.is_symlink():
                raise RuntimeError("Expected the reviewed prepared symbolic-link fixture")
            provided_symbolic.rename(symlink)
            has_symlink = True
        else:
            has_symlink = bool(create_link(str(symlink), str(private), 3))
        if has_symlink:
            report["checks"]["reject_alias_grants"] &= rejected(symlink)
        if not has_symlink:
            error = ctypes.get_last_error()
            if error != 1314:
                raise ctypes.WinError(error)
            report.setdefault("not_executed", {})["symbolic_link"] = (
                "Fixture creation requires SeCreateSymbolicLinkPrivilege or Developer Mode; "
                "neither was enabled by this probe (WinError 1314)."
            )
        result = launch(
            0, "--paths", "--other", str(private), *(["--check-symlink"] if has_symlink else [])
        )
        report["results"]["path_aliases"] = result
        data = json.loads(result["stdout"])
        report["checks"]["path_aliases"] = (
            result["exit_code"] == 0
            and result["job_drained"]
            and set(data)
            == (
                {"junction", "hardlink", "replaced", "create_hardlink"}
                | ({"symlink"} if has_symlink else set())
            )
            and all(not value["allowed"] and value["errno"] == 13 for value in data.values())
        )
        save()
    finally:
        # Unlink only the exact newly-created directory entries, never traverse them.
        for target in (junction, symlink, replacement):
            if os.path.lexists(target):
                if not target.lstat().st_file_attributes & 0x400:
                    raise RuntimeError("Expected owned reparse point before cleanup")
                target.rmdir()
        if hardlink.exists():
            hardlink.unlink()
        report["checks"]["alias_target_unchanged"] = (private / "sample.txt").read_text() == "C"
        if provided_symbolic is not None:
            report["checks"]["alias_target_unchanged"] &= (
                (provided_symbolic.parent / "Private/sample.txt").read_text() == "C")
        report["symbolic_link_cleaned"] = not os.path.lexists(symlink)
        save()
