"""Bounded administrator experiment: add fixed query ACEs, wait, then remove.

Accepts an experiment UUID and optional fixed symbolic fixture creation. It cannot
execute commands or accept target paths,
change ownership, grant network access, or interpret requests from the LPAC child.
The ordinary host runs probes separately. The stop file only requests cleanup.
"""

import argparse
import ctypes
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

# -I excludes the script directory; restore only this reviewed source directory.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from lpac_probe.namespace import (  # noqa: E402
    TARGETS,
    capability_name,
    describe_acl,
    entries_for_sid,
    open_target,
    read_acl,
    remove_owned_ace,
    write_acl,
)
from lpac_probe.native import system_capability_sids  # noqa: E402


def main():
    import win32security as security

    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--symbolic-fixture", action="store_true")
    args = parser.parse_args()
    name = capability_name(args.experiment)
    root = Path(__file__).resolve().parents[1] / "var"
    for parent in (root, *root.parents):
        if parent.lstat().st_file_attributes & 0x400:
            raise RuntimeError("Refuse a reparse point in the diagnostic output path")
    report_path = root / ("l4a-namespace-" + args.experiment + ".json")
    stop_path = report_path.with_suffix(".stop")
    if report_path.exists() or stop_path.exists():
        raise RuntimeError("Experiment identifier already used")
    report = {
        "experiment": args.experiment,
        "elevated": bool(ctypes.windll.shell32.IsUserAnAdmin()),
        "status": "preflight",
        "targets": [],
        "cleanup_errors": [],
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "max_hold_seconds": 600,
    }

    # Open once exclusively; updates never follow a replacement file or link.
    with report_path.open("x", encoding="utf-8") as report_file:

        def save():
            report_file.seek(0)
            report_file.write(json.dumps(report, indent=2))
            report_file.truncate()
            report_file.flush()
            os.fsync(report_file.fileno())

        handles, managed = [], []
        save()
        try:
            if args.apply and not report["elevated"]:
                raise RuntimeError("Apply requires the explicitly approved UAC initialization")
            sids = system_capability_sids(name)
            if len(sids) != 1:
                raise RuntimeError("Expected exactly one derived capability SID")
            sid = sids[0]
            report["capability_sid"] = sid
            # Acquire/query every handle before the first mutation.
            for target in TARGETS:
                handle = open_target(target)
                handles.append((target, handle))
                acl = read_acl(handle)
                if entries_for_sid(acl, sid):
                    raise RuntimeError("Fresh capability already appears in a target ACL")
                report["targets"].append({**asdict(target), "before": describe_acl(acl)})
                save()
            if not args.apply:
                report["status"] = "preview_only"
                return
            if args.symbolic_fixture:
                from lpac_probe.symbolic_fixture import create

                report["symbolic_fixture"] = {"state": "intent"}
                save()
                report["symbolic_fixture"] = create(root.parent, args.experiment)
                save()
            for index, (target, handle) in enumerate(handles):
                report["targets"][index]["state"] = "intent"
                managed.append((index, target, handle))
                save()  # Recovery intent precedes every Windows ACL mutation.
                acl = read_acl(handle)
                if entries_for_sid(acl, sid):
                    raise RuntimeError("Namespace ACL changed during initialization")
                acl.AddAccessAllowedAceEx(
                    security.ACL_REVISION_DS,
                    0,
                    target.mask,
                    security.ConvertStringSidToSid(sid),
                )
                write_acl(handle, acl)
                observed = entries_for_sid(read_acl(handle), sid)
                if len(observed) != 1 or observed[0][1][:2] != ((0, 0), target.mask):
                    raise RuntimeError("Cannot verify the exact namespace ACE")
                report["targets"][index]["state"] = "applied"
                save()
            report["status"] = "ready"
            save()
            deadline = time.monotonic() + 600
            while not stop_path.exists() and time.monotonic() < deadline:
                time.sleep(0.2)
            report["stop_reason"] = "host_requested" if stop_path.exists() else "deadline"
        except BaseException as exc:
            report["error"] = str(exc)
            report["traceback"] = traceback.format_exc()
            report["status"] = "failed"
        finally:
            if "symbolic_fixture" in report:
                try:
                    from lpac_probe.symbolic_fixture import cleanup

                    report["symbolic_fixture"]["cleanup"] = cleanup(root.parent, args.experiment)
                except Exception as exc:
                    report["cleanup_errors"].append({"fixture": "symbolic_link", "error": str(exc)})
            for index, target, handle in reversed(managed):
                try:
                    acl = read_acl(handle)
                    if remove_owned_ace(acl, sid, target.mask):
                        write_acl(handle, acl)
                    verified = read_acl(handle)
                    if entries_for_sid(verified, sid):
                        raise RuntimeError("Namespace ACE removal could not be verified")
                    report["targets"][index].update(state="removed", after=describe_acl(verified))
                except BaseException as exc:
                    report["cleanup_errors"].append({"path": target.path, "error": str(exc)})
                save()
            for _, handle in handles:
                handle.Close()
            if report["cleanup_errors"]:
                report["status"] = "repair_required"
            elif managed and not report.get("error"):
                report["status"] = "cleaned"
            save()
        if report.get("error") or report["cleanup_errors"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
