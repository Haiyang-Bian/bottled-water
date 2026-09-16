"""P1b movement/reused-capability gate, only against a new repository-local tree.

The stale generation is deliberately exercised as an adversarial diagnostic.
A fresh generation is a control, not proof of a production invalidation manager.
An actual bypass always leaves the P1b gate closed, even if that control passes.
"""

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
from uuid import uuid4

from lpac_probe.movement import bypasses, evaluate, subset_passed
from lpac_probe.native import LpacProfile, system_capability_sids
from lpac_probe.standing import (
    MODIFY, READ_EXECUTE, add_aces, cleanup_tree, describe, environment, fixture_acl, tree,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("Windows is required")
    if ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Run as the ordinary user")
    import win32api

    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if (output.parent != repo / "var" or not output.name.startswith("l4a-movement-")
            or output.exists()):
        parser.error("Use a new repository-local var/l4a-movement-* directory")
    filesystem = win32api.GetVolumeInformation(output.anchor)[4]
    if filesystem != "NTFS":
        parser.error("Local NTFS is required")
    output.mkdir(parents=True)
    root, runtime = output / "Tree", output / "Runtime"
    for name in ("Work", "Archive", "Private"):
        (root / name).mkdir(parents=True)
        (root / name / "sample.txt").write_text(name.lower(), encoding="utf-8")
    for name, text in (("to-archive.txt", "archive-move"), ("to-private.txt", "private-move"),
                       ("copy.txt", "copy")):
        (root / "Work" / name).write_text(text, encoding="utf-8")
    (root / "Work/folder").mkdir()
    (root / "Work/folder/nested.txt").write_text("nested", encoding="utf-8")
    runtime.mkdir()
    python = runtime / "python"
    python.mkdir()
    base = Path(sys.base_prefix)
    for pattern in ("*.exe", "*.dll"):
        for source in base.glob(pattern):
            shutil.copyfile(source, python / source.name)
    for name in ("Lib", "DLLs"):
        shutil.copytree(base / name, python / name,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"))
    shutil.copyfile(repo / "scripts/lpac-movement-payload.py", runtime / "payload.py")
    profiles = [LpacProfile() for _ in range(4)]
    generations = [uuid4().hex, uuid4().hex]
    sids = [system_capability_sids("AgentHub.Probe.Policy." + g)[0] for g in generations]
    report = {
        "gate": "not_passed", "subset_passed": False,
        "os_build": str(sys.getwindowsversion()), "filesystem": filesystem,
        "source_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "source_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in (Path(__file__), repo / "scripts/lpac-movement-payload.py",
                                    repo / "scripts/lpac_probe/movement.py",
                                    repo / "scripts/lpac_probe/standing.py",
                                    repo / "scripts/lpac_probe/native.py")},
        "grants": [], "profiles": [], "results": {}, "checks": {}, "movements": [],
        "bypasses": {}, "generation_sids": sids,
        "runtime": {"executable": str(python / "python.exe"), "version": sys.version,
                    "sha256": hashlib.sha256((python / "python.exe").read_bytes()).hexdigest()},
        "not_executed": ["production_cache_invalidation", "active_run_move_race",
                         "complete_toolchain", "full_P1b"],
    }

    def save():
        pending = output / "report.pending"
        with pending.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, output / "report.json")

    def grant(path, sid, entries):
        record = {"path": str(path.relative_to(output)), "sid": sid,
                  "entries": entries, "state": "intent"}
        report["grants"].append(record)
        save()
        add_aces(path, sid, entries)
        record["state"] = "applied"
        save()

    def prepare(index):
        started = time.monotonic()
        grant(root / "Work", sids[index], [(0, 0, MODIFY & ~0x10000), (0, 11, MODIFY)])
        grant(root / "Archive", sids[index], [(0, 3, READ_EXECUTE)])
        report.setdefault("preparation_seconds", []).append(time.monotonic() - started)
        save()

    def inspect(path):
        stat = path.stat()
        return {"volume": stat.st_dev, "file_id": stat.st_ino,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None,
                "acl": describe(path, sids[0])}

    def transfer(source, target, *, copy=False):
        record = {"source": str(source.relative_to(root)), "target": str(target.relative_to(root)),
                  "operation": "copy" if copy else "rename", "before": inspect(source),
                  "state": "intent"}
        report["movements"].append(record)
        save()
        if copy:
            shutil.copy2(source, target)
        else:
            source.rename(target)
        record.update(after=inspect(target), state="completed")
        record["same_object"] = all(record["before"][k] == record["after"][k]
                                    for k in ("volume", "file_id"))
        save()

    def run(name, profile_index, generation, *, moved=False):
        # Reset file contents only, not ACLs, so each diagnostic has identical positive controls.
        (root / "Work/sample.txt").write_text("work", encoding="utf-8")
        if moved:
            (root / "Archive/from-work.txt").write_text("archive-move", encoding="utf-8")
        scratch = output / f"Scratch{profile_index}"
        outcome = profiles[profile_index].run(
            [str(python / "python.exe"), "-I", "-S", "-B", str(runtime / "payload.py"),
             "--root", str(root), *(["--moved"] if moved else [])],
            scratch, environment=environment(scratch), registry_read=True,
            policy_experiment=generations[generation], timeout=30,
        )
        report["results"][name] = outcome
        report["checks"][name] = evaluate(outcome, sids[generation], moved=moved)
        report["bypasses"][name] = bypasses(outcome, report["checks"][name])
        save()

    save()
    try:
        prepare(0)
        report["initial_root"] = inspect(root / "Work")
        for index, profile in enumerate(profiles):
            report["profiles"].append({"name": profile.name, "state": "intent"})
            save()
            profile.create()
            report["profiles"][-1].update(sid=profile.sid, state="created")
            save()
            scratch = output / f"Scratch{index}"
            scratch.mkdir()
            grant(runtime, profile.sid, [(0, 3, READ_EXECUTE)])
            grant(scratch, profile.sid, [(0, 3, MODIFY)])
        run("baseline", 0, 0)
        if not all(report["checks"]["baseline"].values()):
            raise RuntimeError("Baseline failed: movement evidence would not be valid")
        transfer(root / "Work/to-archive.txt", root / "Archive/from-work.txt")
        transfer(root / "Work/to-private.txt", root / "Private/from-work.txt")
        transfer(root / "Work/folder", root / "Private/folder")
        transfer(root / "Work/copy.txt", root / "Archive/copy.txt", copy=True)
        transfer(root / "Work/copy.txt", root / "Private/copy.txt", copy=True)
        # The root is still identical here: any bypass is caused by moved children,
        # not by a failure to notice that the root itself was replaced.
        (root / "DetachedWork").mkdir()
        (root / "DetachedWork/sample.txt").write_text("detached", encoding="utf-8")
        report["root_before_children_reuse"] = inspect(root / "Work")
        run("reused_after_children_moved", 1, 0, moved=True)
        # Move the initial unauthorized control aside without removing evidence.
        (root / "DetachedWork").rename(root / "DetachedControl")
        transfer(root / "Work", root / "DetachedWork")
        (root / "Work").mkdir()
        (root / "Work/sample.txt").write_text("work", encoding="utf-8")
        report["replacement_root"] = inspect(root / "Work")
        # The unchanged root path has a NEW object identity. Do not regrant the stale SID:
        # failure to access the replacement is also evidence that path-only reuse is invalid.
        run("reused_after_root_replaced", 2, 0, moved=True)
        # New identity is tested ONLY as a control, never as an eraser for the stale bypass.
        prepare(1)
        run("fresh_generation", 3, 1, moved=True)
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()}
    finally:
        if any(profile.cleanup_blocked for profile in profiles):
            report["cleanup_error"] = "Job not confirmed drained; retained identities and ACLs"
        else:
            try:
                owned = set(sids) | {profile.sid for profile in profiles if profile.sid}
                before = {str(path.relative_to(output)): fixture_acl(path) for path in tree(output)}
                report["cleanup_intent"] = "remove only recorded principals from owned fixture"
                save()
                report["cleanup_objects"] = cleanup_tree(output, owned)
                report["unrelated_acl_preserved"] = all(
                    fixture_acl(output / path) == {**acl, "aces": [ace for ace in acl["aces"]
                                                                       if ace[-1] not in owned]}
                    for path, acl in before.items())
                for record, profile in zip(report["profiles"], profiles):
                    if profile.sid:
                        profile.delete()
                        record["deleted"] = True
                report["cleanup_verified"] = report["unrelated_acl_preserved"] and all(
                    record.get("deleted") is True for record in report["profiles"])
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        report["subset_passed"] = subset_passed(report)
        report["stop_reason"] = (
            "stale_policy_access" if any(report["bypasses"].values()) else
            "evidence_incomplete" if not report["subset_passed"] else "full_P1b_not_executed"
        )
        save()
    print(json.dumps({"report": str(output / "report.json"),
                      "subset_passed": report["subset_passed"], "bypasses": report["bypasses"],
                      "error": report.get("error"), "cleanup_verified": report.get("cleanup_verified")}))
    return 0 if report["subset_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
