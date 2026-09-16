"""Explicit stop/withdraw/reprepare native subset; no model, admin or production setup."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback

from lpac_probe.movement import evaluate
from lpac_probe.native import LpacProfile
from lpac_probe.quiescent import FixturePreparationBackend
from lpac_probe.standing import MODIFY, READ_EXECUTE, add_aces, cleanup_tree, environment


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--toolchain", action="store_true")
    parser.add_argument("--namespace-experiment")
    args = parser.parse_args()
    if sys.platform != "win32" or ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Requires an ordinary Windows user")
    import win32api

    repo = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo / "src"))
    from agent_contracts.errors import OperationError
    from agent_contracts.permissions import PathPermission, StandingPermissionPolicy
    from agent_subsystems.workspaces.permission_preparation import PreparedPolicy
    from agent_subsystems.workspaces.permissions import freeze_policy

    output = args.output.resolve()
    if (output.parent != repo / "var" or not output.name.startswith("l4a-quiescent-")
            or output.exists() or win32api.GetVolumeInformation(output.anchor)[4] != "NTFS"):
        parser.error("Use a fresh var/l4a-quiescent-* directory on local NTFS")
    root, runtime = output / "Tree", output / "Runtime"
    if args.namespace_experiment:
        from lpac_probe.namespace import capability_name

        capability_name(args.namespace_experiment)
        initialization = json.loads((repo / "var" / (
            "l4a-namespace-" + args.namespace_experiment + ".json")).read_text())
        if initialization.get("status") != "ready" or not initialization.get("elevated"):
            raise RuntimeError("Fixed namespace initialization is not ready")
    for name in ("Work", "Archive", "Private", "DetachedWork"):
        (root / name).mkdir(parents=True)
        (root / name / "sample.txt").write_text(name.lower(), encoding="utf-8")
    for name, content in (("archive.txt", "archive-move"), ("private.txt", "private")):
        (root / "Work" / name).write_text(content, encoding="utf-8")
    (root / "Work/folder").mkdir()
    (root / "Work/folder/nested.txt").write_text("nested", encoding="utf-8")
    (root / "Archive/copy.txt").write_text("copy", encoding="utf-8")
    (root / "Private/copy.txt").write_text("private", encoding="utf-8")
    python = runtime / "python"
    python.mkdir(parents=True)
    base = Path(sys.base_prefix)
    for pattern in ("*.exe", "*.dll"):
        for source in base.glob(pattern):
            shutil.copyfile(source, python / source.name)
    for name in ("Lib", "DLLs"):
        shutil.copytree(base / name, python / name,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"))
    shutil.copyfile(repo / "scripts/lpac-movement-payload.py", runtime / "payload.py")
    (runtime / "hold.py").write_text(
        "import pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text('ready')\n"
        "time.sleep(60)\n", encoding="utf-8")
    toolchain_manifest = None
    if args.toolchain:
        from lpac_probe.toolchain import prepare as prepare_toolchain

        toolchain_manifest = prepare_toolchain(runtime, root)
    dependency_files = (toolchain_manifest["files"] if toolchain_manifest else {
        str(path.relative_to(runtime)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in runtime.rglob("*") if path.is_file()})
    dependency_digest = hashlib.sha256(json.dumps(
        dependency_files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    profiles = [LpacProfile() for _ in range(5)]
    preparations = []
    report = {"gate": "not_passed", "subset_passed": False, "checks": {}, "results": {},
              "profiles": [], "package_grants": [], "os_build": str(sys.getwindowsversion()),
              "source_commit": subprocess.check_output(
                  ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
              "source_sha256": {str(path.relative_to(repo)):
                  hashlib.sha256(path.read_bytes()).hexdigest() for path in (
                      Path(__file__), repo / "scripts/lpac_probe/quiescent.py",
                      repo / "scripts/lpac_probe/native.py", repo / "scripts/lpac_probe/standing.py",
                      repo / "scripts/lpac_probe/movement.py", repo / "scripts/lpac-movement-payload.py",
                      repo / "scripts/lpac_probe/toolchain.py",
                      repo / "src/agent_subsystems/workspaces/permission_preparation.py")},
              "not_executed": ["full_P1b", "cross_host_withdrawal", "live_move_revocation",
                               "production_file_worker", "complete_toolchain"]}
    report["toolchain_manifest"] = toolchain_manifest
    report["dependency_digest"] = dependency_digest
    report["dependency_files"] = dependency_files
    report["namespace_experiment"] = args.namespace_experiment

    def save():
        pending = output / "report.pending"
        with pending.open("w", encoding="utf-8") as file:
            json.dump(report, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, output / "report.json")

    backend = FixturePreparationBackend(root, report, save)

    def new_preparation(access, revision):
        snapshot = freeze_policy(StandingPermissionPolicy("probe", "local", revision, (
            PathPermission(root / "Work", access), PathPermission(root / "Archive", "read")),
            enabled=True))
        prepared = PreparedPolicy(snapshot, dependency_digest, backend)
        preparations.append(prepared)
        prepared.prepare()
        return prepared

    def execute(index, generation, *, moved=False):
        return profiles[index].run(
            [str(python / "python.exe"), "-I", "-S", "-B", str(runtime / "payload.py"),
             "--root", str(root), *(["--moved"] if moved else [])],
            output / f"Scratch{index}", environment=environment(output / f"Scratch{index}"),
            registry_read=True, policy_experiment=generation, timeout=15)

    def denied(item):
        return item.get("allowed") is False and (item.get("winerror") == 5 or item.get("errno") == 13)

    save()
    try:
        old = new_preparation("modify", 1)
        for index, profile in enumerate(profiles):
            record = {"name": profile.name, "state": "intent"}
            report["profiles"].append(record)
            save()
            profile.create()
            record.update(sid=profile.sid, state="created")
            scratch = output / f"Scratch{index}"
            scratch.mkdir()
            for path, mask in ((runtime, READ_EXECUTE), (scratch, MODIFY)):
                intent = {"path": str(path), "sid": profile.sid, "mask": mask, "state": "intent"}
                report["package_grants"].append(intent)
                save()
                add_aces(path, profile.sid, [(0, 3, mask)])
                intent["state"] = "applied"
                save()
        lease = old.acquire("baseline")
        result = execute(0, old.generation)
        lease.finish(job_drained=result["job_drained"])
        report["results"]["baseline"] = result
        sid = backend.records[old.generation]["sid"]
        report["checks"]["baseline"] = all(evaluate(result, sid).values())
        if not report["checks"]["baseline"]:
            raise RuntimeError("Positive baseline failed")
        if args.toolchain:
            from lpac_probe.toolchain import run as run_toolchain

            lease = old.acquire("toolchain-modify")
            try:
                report["toolchain_modify"] = run_toolchain(
                    profiles[0], old.generation, sid, runtime, root, output / "Scratch0",
                    namespace=args.namespace_experiment)
            finally:
                lease.finish(job_drained=not profiles[0].cleanup_blocked)
            save()

        # Two concurrent Runs share the generation. Permission edits require both Jobs to exit.
        cancels = [threading.Event(), threading.Event()]
        leases = [old.acquire(f"hold-{index}") for index in (1, 2)]

        def hold(index):
            scratch = output / f"Scratch{index + 1}"
            return profiles[index + 1].run(
                [str(python / "python.exe"), "-I", "-S", "-B", str(runtime / "hold.py"),
                 str(scratch / "ready")], scratch, environment=environment(scratch),
                registry_read=True, policy_experiment=old.generation,
                cancel_event=cancels[index], timeout=15)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(hold, index) for index in range(2)]
            try:
                deadline = time.monotonic() + 8
                while not all((output / f"Scratch{index}/ready").is_file() for index in (1, 2)):
                    if time.monotonic() > deadline or any(future.done() for future in futures):
                        raise RuntimeError("Concurrent LPAC processes did not become ready")
                    time.sleep(0.02)
                report["checks"]["overlap"] = not any(future.done() for future in futures)
                try:
                    old.retire()
                except OperationError as exc:
                    report["checks"]["busy_refused"] = exc.code == "permission_busy"
                else:
                    raise RuntimeError("Withdrawal accepted while two Jobs were active")
            finally:
                for cancel in cancels:
                    cancel.set()
                for index, future in enumerate(futures):
                    result = future.result(timeout=10)
                    report["results"][f"hold-{index}"] = result
                    leases[index].finish(job_drained=result["job_drained"])
            report["checks"]["cancelled_and_drained"] = all(
                report["results"][f"hold-{i}"]["cancelled"]
                and report["results"][f"hold-{i}"]["job_drained"] for i in range(2))
        old.retire()
        report["checks"]["old_retired"] = old.state == "retired" and not old.active_runs
        try:
            old.acquire("late-run")
        except OperationError as exc:
            report["checks"]["late_launch_refused"] = exc.code == "permission_generation_unavailable"
        else:
            raise RuntimeError("Retired generation was reused")
        # Raw diagnostic bypasses the host coordinator ONLY to prove the old ACL was removed.
        result = execute(3, old.generation)
        report["results"]["retired_native_identity"] = result
        values = json.loads(result["stdout"])
        checks = evaluate(result, sid)
        report["checks"]["retired_native_denied"] = checks["launcher"] and checks["identity"] and all(
            denied(values[name]) for name in ("read_work", "write_work", "read_archive",
                                              "write_archive", "read_private", "write_private"))
        if not report["checks"]["retired_native_denied"]:
            raise RuntimeError("Old native identity still has access after withdrawal")

        # Only now does the trusted user reorganize the fixture. No live move guarantee is claimed.
        (root / "Work/archive.txt").rename(root / "Archive/from-work.txt")
        (root / "Work/private.txt").rename(root / "Private/from-work.txt")
        (root / "Work/folder").rename(root / "Private/folder")
        (root / "Work/sample.txt").write_text("work", encoding="utf-8")
        current = new_preparation("read", 2)
        lease = current.acquire("readonly-task")
        result = execute(4, current.generation, moved=True)
        lease.finish(job_drained=result["job_drained"])
        report["results"]["readonly_task"] = result
        values = json.loads(result["stdout"])
        checks = evaluate(result, backend.records[current.generation]["sid"], moved=True)
        checks["write_work"] = denied(values["write_work"])
        report["checks"]["new_readonly_policy"] = all(checks.values())
        report["checks"]["fresh_generation"] = current.generation != old.generation
        report["checks"]["old_capability_absent"] = sid not in result["token"]["capabilities"]
        if args.toolchain:
            lease = current.acquire("toolchain-readonly")
            try:
                report["toolchain_readonly"] = run_toolchain(
                    profiles[4], current.generation, backend.records[current.generation]["sid"],
                    runtime, root, output / "Scratch4", readonly=True,
                    namespace=args.namespace_experiment)
            finally:
                lease.finish(job_drained=not profiles[4].cleanup_blocked)
        current.retire()
        save()

        # Compensation and failed withdrawal must not create a ready or successful state.
        backend.failure = "prepare"
        try:
            new_preparation("modify", 3)
        except RuntimeError:
            report["checks"]["prepare_rollback"] = preparations[-1].state == "retired"
        else:
            raise RuntimeError("Preparation fault not exercised")
        backend.failure = None
        repair = new_preparation("read", 4)
        backend.failure = "retire"
        try:
            repair.retire()
        except RuntimeError:
            report["checks"]["repair_required"] = repair.state == "repair_required"
        else:
            raise RuntimeError("Unverified cleanup was accepted")
        try:
            repair.acquire("forbidden")
        except OperationError:
            report["checks"]["repair_blocks_launch"] = True
        else:
            raise RuntimeError("Repair state allowed execution")
        backend.failure = None
        repair.retire()
        report["checks"]["repair_verified"] = repair.state == "retired"
    except Exception as exc:
        report["error"] = {"type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()}
    finally:
        backend.failure = None
        if any(profile.cleanup_blocked for profile in profiles):
            report["cleanup_error"] = "Job exit is unconfirmed; retain ACL/identities for repair"
        else:
            try:
                # Only the diagnostic owns this entire tree. The production host cannot force-finish.
                for prepared in preparations:
                    if prepared.active_runs:
                        raise RuntimeError("Unreleased registration; cleanup requires inspection")
                    prepared.retire()
                report["package_cleanup_objects"] = cleanup_tree(
                    output, {profile.sid for profile in profiles if profile.sid})
                for profile, record in zip(profiles, report["profiles"]):
                    if profile.sid:
                        profile.delete()
                        record["deleted"] = True
                report["cleanup_verified"] = all(
                    record.get("cleanup_verified") is True for record in backend.records.values())
            except Exception as exc:
                report["cleanup_error"] = str(exc)
        report["subset_passed"] = (
            not report.get("error") and not report.get("cleanup_error")
            and report.get("cleanup_verified") is True and len(report["checks"]) == 14
            and all(value is True for value in report["checks"].values()))
        if args.toolchain:
            report["toolchain_passed"] = all(report.get(name, {}).get("passed") is True
                                             for name in ("toolchain_modify", "toolchain_readonly"))
            report["subset_passed"] &= report["toolchain_passed"]
            if report["toolchain_passed"]:
                report["not_executed"].remove("complete_toolchain")
        save()
    print(json.dumps({"report": str(output / "report.json"), "subset_passed": report["subset_passed"],
                      "checks": report["checks"], "error": report.get("error"),
                      "cleanup_error": report.get("cleanup_error")}))
    return 0 if report["subset_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
