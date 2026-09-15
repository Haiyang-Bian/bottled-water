"""Native P1b gate for broad grants containing protected regions. No production use."""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
from uuid import uuid4

from lpac_probe.native import LpacProfile, system_capability_sids
from lpac_probe.standing import (
    ALL, CONTENT_WRITE, MODIFY, READ_EXECUTE, SECURITY_WRITE,
    add_aces, audit_allow_masks, cleanup_tree, describe, environment, evaluate,
    fixture_acl, protect_fixture_boundary, replace_owned_allow,
    restore_fixture_inheritance, tree,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--acl-mode", choices=("deny-aces", "allow-only", "inheritance-barrier"),
                        default="deny-aces")
    args = parser.parse_args()
    if ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Execute native attempts as the ordinary user")
    import win32api

    repo = Path(__file__).resolve().parents[1]
    output = Path(args.output).resolve()
    if not output.is_relative_to(repo / "var") or output.exists():
        parser.error("Use a new repository-local var directory")
    filesystem = win32api.GetVolumeInformation(output.anchor)[4]
    if filesystem != "NTFS":
        parser.error("The probe requires local NTFS")
    output.mkdir(parents=True)
    root, runtime = output / "Tree", output / "Runtime"
    for path in (root / "Work", root / "Group/Archive", root / "Private", runtime):
        path.mkdir(parents=True)
    for path, text in ((root / "Work", "work"), (root / "Group/Archive", "archive"),
                       (root / "Private", "private")):
        for name in ("sample.txt", "delete.txt", "rename.txt"):
            (path / name).write_text(text)
    python = runtime / "python"
    python.mkdir()
    base = Path(sys.base_prefix)
    for pattern in ("*.exe", "*.dll"):
        for source in base.glob(pattern):
            shutil.copyfile(source, python / source.name)
    for name in ("Lib", "DLLs"):
        shutil.copytree(base / name, python / name,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"))
    shutil.copyfile(repo / "scripts/lpac-standing-payload.py", runtime / "payload.py")
    profiles = [LpacProfile() for _ in range(3)]
    policies = [uuid4().hex, uuid4().hex]
    sids = [system_capability_sids("AgentHub.Probe.Policy." + name)[0] for name in policies]
    report = {"gate": "not_passed", "acl_mode": args.acl_mode,
              "checks": {}, "results": [], "grants": [],
              "profiles": [], "policies": sids, "os_build": str(sys.getwindowsversion()),
              "filesystem": filesystem,
              "source_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), repo / "scripts/lpac-standing-payload.py",
                                          repo / "scripts/lpac_probe/standing.py",
                                          repo / "scripts/lpac_probe/native.py")}}
    report["original_acl"] = {str(p.relative_to(output)): fixture_acl(p) for p in tree(root)}
    report["inheritance_changes"] = []

    def save():
        pending = output / "report.pending"
        with pending.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, output / "report.json")

    def grant(path, sid, entries):
        report["grants"].append({"path": str(path.relative_to(output)), "sid": sid,
                                 "entries": entries, "state": "intent"})
        save()
        add_aces(path, sid, entries)
        report["grants"][-1]["state"] = "applied"
        save()

    save()
    try:
        start = time.monotonic()
        # Add protection first, then inherited permissions to the broad tree.
        grant(root / "Group/Archive", sids[0], [(1, 3, CONTENT_WRITE)])
        grant(root / "Private", sids[0], [(1, 3, ALL)])
        grant(root / "Group", sids[0], [(1, 0, 0x10000)])
        grant(root, sids[0], [(1, 0, 0x10000), (1, 3, SECURITY_WRITE), (0, 3, MODIFY)])
        grant(root / "Work", sids[1], [(1, 3, CONTENT_WRITE), (0, 3, READ_EXECUTE)])
        targets = [root, root / "Work", root / "Group", root / "Group/Archive",
                   root / "Group/Archive/sample.txt", root / "Private", root / "Private/sample.txt"]
        report["before_repair"] = {str(p.relative_to(root)): describe(p, sids[0]) for p in targets}
        save()
        if args.acl_mode == "allow-only":
            # Keep the user's existing ACL and inheritance control bits intact. This
            # intentionally tests whether selective ACE removal alone can be sufficient.
            report["repair_intent"] = "replace only current policy ACEs, preserve user ACL inheritance"
            save()
            replace_owned_allow(root, sids[0], MODIFY & ~0x10000)
            replace_owned_allow(root / "Work", sids[0], MODIFY)
            replace_owned_allow(root / "Group", sids[0], MODIFY & ~0x10000)
            for path in tree(root / "Group/Archive"):
                replace_owned_allow(path, sids[0], READ_EXECUTE)
            for path in tree(root / "Private"):
                replace_owned_allow(path, sids[0], 0)
        elif args.acl_mode == "inheritance-barrier":
            # Diagnostic only: changing inheritance freezes other principals too.
            # Explicit ancestor rights exclude DELETE; inherit-only rights preserve
            # normal creation/deletion for unprotected descendants.
            for path, entries in (
                (root, [(0, MODIFY & ~0x10000), (11, MODIFY)]),
                (root / "Group", [(0, MODIFY & ~0x10000), (11, MODIFY)]),
                (root / "Group/Archive", [(3, READ_EXECUTE)]),
                (root / "Private", []),
            ):
                report["inheritance_changes"].append({"path": str(path.relative_to(output)),
                    "originally_protected": report["original_acl"][str(path.relative_to(output))]
                    ["protected"], "state": "intent"})
                save()
                protect_fixture_boundary(path, sids[0], entries)
                report["inheritance_changes"][-1]["state"] = "applied"
                save()
        report["prepared_acl"] = {str(p.relative_to(root)): describe(p, sids[0]) for p in targets}
        limits = {str(p.relative_to(root)): (
            0 if p.is_relative_to(root / "Private") else READ_EXECUTE
        ) for p in targets if p.is_relative_to(root / "Private")
                  or p.is_relative_to(root / "Group/Archive")}
        report["preparation_violations"] = audit_allow_masks(report["prepared_acl"], limits)
        report["preparation_usable"] = not report["preparation_violations"]
        # Diagnostic execution intentionally demonstrates rejection/bypass against
        # disposable fixtures. A production launcher must refuse an unusable policy.
        report["preparation_seconds"] = time.monotonic() - start
        prepared_count = len(report["grants"])
        for index, profile in enumerate(profiles):
            profile.create()
            report["profiles"].append({"name": profile.name, "sid": profile.sid})
            save()
            scratch = output / f"Scratch{index}"
            scratch.mkdir()
            (scratch / "private.txt").write_text(f"private-{index}")
            grant(runtime, profile.sid, [(0, 3, READ_EXECUTE)])
            grant(scratch, profile.sid, [(0, 3, MODIFY)])
        for index, profile in enumerate(profiles):
            # Restore ordinary positive controls between successful broad executions.
            (root / "Work/renamed.txt").unlink(missing_ok=True)
            for name in ("delete.txt", "rename.txt", "sample.txt"):
                (root / "Work" / name).write_text("work")
            narrow = index == 2
            policy_index = 1 if narrow else 0
            scratch = output / f"Scratch{index}"
            started = time.monotonic()
            outcome = profile.run(
                [str(python / "python.exe"), "-I", "-S", "-B", str(runtime / "payload.py"),
                 "--root", str(root), "--scratch", str(scratch),
                 "--other-scratch", str(output / f"Scratch{(index + 1) % 3}"),
                 *(["--narrow"] if narrow else [])],
                scratch, environment=environment(scratch), registry_read=True,
                policy_experiment=policies[policy_index], timeout=30,
            )
            report["results"].append(outcome)
            save()
            values = json.loads(outcome["stdout"])
            checks = evaluate(values, narrow)
            report["checks"][f"run_{index}"] = (
                outcome["exit_code"] == 0 and outcome["job_drained"]
                and all(checks.values()) and sids[policy_index] in outcome["token"]["capabilities"]
                and sids[1 - policy_index] not in outcome["token"]["capabilities"]
            )
            report.setdefault("attempt_checks", []).append(checks)
            report.setdefault("execution_seconds", []).append(time.monotonic() - started)
            save()
            if not report["checks"][f"run_{index}"]:
                # Preserve failing facts and stop before additional toolchain/production work.
                report["failure"] = {"run": index, "checks": [k for k, v in checks.items() if not v]}
                break
        report["checks"]["protected_content_unchanged"] = all(
            path.is_file() and path.read_text() == expected
            for path, expected in ((root / "Group/Archive/sample.txt", "archive"),
                                   (root / "Private/sample.txt", "private"))
        )
        report["checks"]["business_acl_prepared_once"] = (
            len(report["results"]) >= 2 and len(report["grants"]) == prepared_count + 6
        )
        report["checks"]["unique_packages"] = len({p.sid for p in profiles}) == 3
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()}
    finally:
        if any(p.cleanup_blocked for p in profiles):
            report["cleanup_error"] = "A Job is not confirmed drained; retained identities/ACLs"
        else:
            try:
                report["cleanup_objects"] = cleanup_tree(
                    output, set(sids) | {p.sid for p in profiles if p.sid})
                for change in report["inheritance_changes"]:
                    restore_fixture_inheritance(output / change["path"],
                                                report["original_acl"][change["path"]])
                    change["state"] = "restored"
                if report["inheritance_changes"]:
                    report["unrelated_acl_preserved"] = all(
                        (output / path).exists() and fixture_acl(output / path) == original
                        for path, original in report["original_acl"].items()
                        if not path.startswith("Tree\\Work\\")
                    )
                for record, profile in zip(report["profiles"], profiles):
                    profile.delete()
                    record["deleted"] = True
                report["cleanup_verified"] = report.get("unrelated_acl_preserved", True)
                if not report["cleanup_verified"]:
                    report["cleanup_error"] = "Original fixture ACL or inheritance not preserved"
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        report["subset_passed"] = (
            not report.get("error") and not report.get("failure")
            and report.get("preparation_usable") is True
            and report.get("cleanup_verified") is True and len(report["results"]) == 3
            and all(report["checks"].values())
        )
        save()
    print(json.dumps({"report": str(output / "report.json"), "subset_passed": report["subset_passed"],
                      "failure": report.get("failure"), "error": report.get("error"),
                      "cleanup_verified": report.get("cleanup_verified")}))
    return 0 if report["subset_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
