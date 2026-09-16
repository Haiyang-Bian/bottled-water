"""Native P1 concurrency, process access, handle and lifecycle evidence.

Owns only new repository-local fixtures. No administrator or system ACL changes.
Does not claim full P1 acceptance or install any production execution components.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback

from lpac_probe.native import LpacProfile
from lpac_probe.adversarial import aliases, startup_faults


def main():
    import win32con
    import win32api
    import win32event
    import win32file
    import win32job
    import win32security as security

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--crash-host", action="store_true")
    parser.add_argument("--nested-jobs", action="store_true")
    parser.add_argument("--symbolic-fixture-id")
    args = parser.parse_args()
    if ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("The probe must run as the ordinary user")
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if output.parent != repo / "var" or not output.name.startswith("l4a-isolation-"):
        parser.error("Use a new var/l4a-isolation-* directory")
    if args.crash_host:
        plan = json.loads((output / "crash.json").read_text())
        profile = LpacProfile(plan["name"])
        profile.sid = plan["sid"]
        profile.run(
            plan["argv"],
            output / "A",
            environment=plan["environment"],
            registry_read=True,
            timeout=90,
        )
        return
    if output.exists():
        parser.error("Output already exists")
    output.mkdir()
    for part in ("A", "B", "C", "R"):
        (output / part).mkdir()
        (output / part / "sample.txt").write_text(part)
    runtime = output / "R/python"
    runtime.mkdir()
    base = Path(sys.base_prefix)
    for pattern in ("*.exe", "*.dll"):
        for source in base.glob(pattern):
            shutil.copyfile(source, runtime / source.name)
    for name in ("Lib", "DLLs"):
        shutil.copytree(
            base / name,
            runtime / name,
            ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"),
        )
    shutil.copyfile(repo / "scripts/lpac-isolation-payload.py", output / "R/payload.py")
    python = str(runtime / "python.exe")
    prefix = [python, "-I", "-S", "-B", str(output / "R/payload.py")]
    profiles = [LpacProfile(), LpacProfile()]
    from lpac_probe.jobs import OwnedJob

    run_jobs = [OwnedJob(), OwnedJob()] if args.nested_jobs else []
    report = {
        "profiles": [{"name": p.name} for p in profiles],
        "results": {},
        "grants": [],
        "cleanup": [],
        "checks": {},
        "p1_release_gate": "not_passed",
        "os_build": str(sys.getwindowsversion()),
        "nested_jobs": args.nested_jobs,
        "host_in_outer_job": bool(win32job.IsProcessInJob(win32api.GetCurrentProcess(), None)),
        "source_sha256": {
            str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (
                Path(__file__),
                repo / "scripts/lpac-isolation-payload.py",
                repo / "scripts/lpac_probe/native.py",
                repo / "scripts/lpac_probe/adversarial.py",
                repo / "scripts/lpac_probe/jobs.py",
            )
        },
    }

    def save():
        pending = output / "report.pending"
        with pending.open("w", encoding="utf-8") as file:
            file.write(json.dumps(report, indent=2))
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, output / "report.json")

    def env(own):
        system = Path(os.environ["SystemRoot"])
        return {
            "SystemRoot": str(system),
            "WINDIR": str(system),
            "SystemDrive": system.drive,
            "PATH": str(system / "System32"),
            "TEMP": str(own),
            "TMP": str(own),
            "USERPROFILE": str(own),
            "LOCALAPPDATA": str(own),
            "APPDATA": str(own),
        }

    def launch(index, *extra, **kwargs):
        own = output / ("A", "B")[index]
        return profiles[index].run(
            prefix + ["--own", str(own), *extra],
            own,
            environment=env(own),
            registry_read=True,
            parent_jobs=(run_jobs[index].handle,) if run_jobs else (),
            **kwargs,
        )

    def live_handles(path, future=None):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if future is not None and future.done():
                raise RuntimeError("LPAC stopped before recording the process tree")
            try:
                pids = json.loads(path.read_text())
                handles = [win32api.OpenProcess(0x100000 | 0x1000, False, pid) for pid in pids]
                if all(win32event.WaitForSingleObject(h, 0) == 258 for h in handles):
                    return pids, handles
                for handle in handles:
                    handle.Close()
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            time.sleep(0.02)
        raise RuntimeError("Could not confirm a live LPAC child and grandchild")

    def wait_dead(handles):
        try:
            return all(win32event.WaitForSingleObject(h, 5000) == 0 for h in handles)
        finally:
            for handle in handles:
                handle.Close()

    secret_handle = None
    previous_sentinel = os.environ.get("AGENTHUB_PROBE_PRIVATE")
    save()
    try:
        for index, profile in enumerate(profiles):
            profile.create()
            report["profiles"][index]["sid"] = profile.sid
            save()
            for directory, mask in (("R", 0x1200A9), (("A", "B")[index], 0x1301BF)):
                target = output / directory
                report["grants"].append({"path": directory, "sid": profile.sid, "state": "intent"})
                save()
                sd = security.GetNamedSecurityInfo(str(target), security.SE_FILE_OBJECT, 4)
                acl = sd.GetSecurityDescriptorDacl()
                if acl is None:
                    raise RuntimeError("NULL fixture DACL")
                acl.AddAccessAllowedAceEx(4, 3, mask, security.ConvertStringSidToSid(profile.sid))
                security.SetNamedSecurityInfo(
                    str(target), security.SE_FILE_OBJECT, 4, None, None, acl, None
                )
                report["grants"][-1]["state"] = "applied"
                save()
        attrs = security.SECURITY_ATTRIBUTES()
        attrs.bInheritHandle = True
        secret_handle = win32file.CreateFile(
            str(output / "C/sample.txt"), win32con.GENERIC_READ, 7, attrs, 3, 0, None
        )
        os.environ["AGENTHUB_PROBE_PRIVATE"] = "sentinel-not-a-real-credential"
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(
                    launch,
                    i,
                    "--other",
                    str(output / ("B", "A")[i]),
                    "--host",
                    str(os.getpid()),
                    "--handle",
                    str(int(secret_handle)),
                )
                for i in range(2)
            ]
            for index, future in enumerate(futures):
                report["results"][f"identity_{index}"] = future.result()
                save()
            for index in range(2):
                result = report["results"][f"identity_{index}"]
                data = json.loads(result["stdout"])
                report["checks"][f"identity_{index}"] = (
                    result["exit_code"] == 0
                    and result["job_drained"]
                    and all(result["token"][key] for key in ("appcontainer", "lpac", "sid_matches"))
                    and data["read_own"] == {"allowed": True, "value": ("A", "B")[index]}
                    and data["write_own"]["allowed"]
                    and all(
                        not data[key]["allowed"] and data[key]["errno"] == 13
                        for key in ("read_other", "write_other")
                    )
                    and all(
                        not data[key]["allowed"] and data[key]["winerror"] == 5
                        for key in (
                            "host_read_memory",
                            "host_duplicate_handle",
                            "host_create_thread",
                            "host_terminate",
                        )
                    )
                    and not data["unlisted_handle"]["query_succeeded"]
                    and data["unlisted_handle"]["winerror"] in (6, -1073741816)
                    and not data["private_env_present"]
                )
        report["checks"]["distinct_identities"] = profiles[0].sid != profiles[1].sid
        windows = [
            json.loads(report["results"][f"identity_{i}"]["stdout"])["window"] for i in range(2)
        ]
        report["checks"]["overlap"] = max(v[0] for v in windows) < min(v[1] for v in windows)
        save()
        for mode in ("cancel", "timeout"):
            pid_path = output / "A/pids.json"
            if pid_path.exists():
                pid_path.unlink()
            cancel = threading.Event()
            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(
                    launch,
                    0,
                    "--sleep",
                    timeout=2 if mode == "timeout" else 30,
                    cancel_event=cancel,
                )
                pids, handles = live_handles(pid_path, future)
                requested = time.monotonic()
                if mode == "cancel":
                    cancel.set()
                result = future.result()
                dead = wait_dead(handles)
            report["results"][mode] = {
                **result,
                "tree_pids": pids,
                "all_dead": dead,
                "termination_wait_seconds": time.monotonic() - requested,
            }
            report["checks"][mode] = (
                dead
                and result["job_drained"]
                and result["exit_code"] is None
                and result["cancelled" if mode == "cancel" else "timed_out"]
            )
            save()
        # Kill an ordinary host while it owns the LPAC Job; keep process handles
        # acquired before the kill so PID reuse cannot counterfeit cleanup evidence.
        (output / "A/pids.json").unlink()
        (output / "crash.json").write_text(
            json.dumps(
                {
                    "name": profiles[0].name,
                    "sid": profiles[0].sid,
                    "argv": prefix + ["--own", str(output / "A"), "--sleep"],
                    "environment": env(output / "A"),
                }
            )
        )
        profiles[0].cleanup_blocked = True
        crash_host = subprocess.Popen(
            [sys.executable, __file__, "--output", str(output), "--crash-host"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=0x08000000,
        )
        try:
            pids, handles = live_handles(output / "A/pids.json")
            requested = time.monotonic()
            crash_host.kill()
            stdout, stderr = crash_host.communicate(timeout=10)
            dead = wait_dead(handles)
            profiles[0].cleanup_blocked = not dead
            report["results"]["host_crash"] = {
                "host_exit_code": crash_host.returncode,
                "tree_pids": pids,
                "all_dead": dead,
                "termination_wait_seconds": time.monotonic() - requested,
                "stdout": stdout.decode(errors="replace"),
                "stderr": stderr.decode(errors="replace"),
            }
            report["checks"]["host_crash"] = dead and crash_host.returncode != 0
        finally:
            if crash_host.poll() is None:
                crash_host.kill()
                crash_host.wait(timeout=10)
        save()
        listeners, ports = [], []
        try:
            for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
                tcp = socket.socket(family, socket.SOCK_STREAM)
                listeners.append(tcp)
                tcp.bind((host, 0))
                tcp.listen(4)
                port = tcp.getsockname()[1]
                ports.append(str(port))
                udp = socket.socket(family, socket.SOCK_DGRAM)
                listeners.append(udp)
                udp.bind((host, port))
                udp.settimeout(2)
                with socket.socket(family, socket.SOCK_STREAM) as peer:
                    peer.connect((host, port))
                    connection, _ = tcp.accept()
                    connection.close()
                with socket.socket(family, socket.SOCK_DGRAM) as peer:
                    peer.sendto(b"host-positive-control", (host, port))
                if udp.recvfrom(100)[0] != b"host-positive-control":
                    raise RuntimeError("Host network positive control failed")
            result = launch(0, "--network", *ports)
            data = json.loads(result["stdout"])
            child = json.loads(data["child"]["stdout"])
            report["results"]["network_descendants"] = result
            report["checks"]["network_descendants"] = (
                result["exit_code"] == 0
                and result["job_drained"]
                and data["child"]["exit_code"] == 0
                and all(
                    not block[key]["allowed"] and block[key]["winerror"] == 10013
                    for block in (data, child)
                    for key in ("ipv4_tcp", "ipv4_udp", "ipv6_tcp", "ipv6_udp")
                )
            )
            save()
        finally:
            for listener in listeners:
                listener.close()
        startup_faults(profiles[0], launch, report, save)
        provided = None
        if args.symbolic_fixture_id:
            from lpac_probe.symbolic_fixture import paths

            _, provided, _ = paths(repo, args.symbolic_fixture_id)
        aliases(output, launch, report, save, provided_symbolic=provided)
    except BaseException as exc:
        report["error"] = str(exc)
        report["traceback"] = traceback.format_exc()
        save()
    finally:
        if secret_handle is not None:
            secret_handle.Close()
        if previous_sentinel is None:
            os.environ.pop("AGENTHUB_PROBE_PRIVATE", None)
        else:
            os.environ["AGENTHUB_PROBE_PRIVATE"] = previous_sentinel
        for parent in run_jobs:
            parent.close()
        report["parent_jobs_closed"] = all(parent.handle is None for parent in run_jobs)
        # Reuse the existing no-follow fixture walker; never follow aliases in cleanup.
        spec = importlib.util.spec_from_file_location(
            "lpac_probe_main", repo / "scripts/probe-windows-lpac.py"
        )
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        if any(profile.cleanup_blocked for profile in profiles):
            report["cleanup_error"] = "A Job cleanup is unconfirmed"
        else:
            try:
                for directory in ("A", "B", "R"):
                    for target in probe.fixture_tree(output / directory):
                        sd = security.GetNamedSecurityInfo(str(target), security.SE_FILE_OBJECT, 4)
                        acl = sd.GetSecurityDescriptorDacl()
                        for index in reversed(range(acl.GetAceCount())):
                            if security.ConvertSidToStringSid(acl.GetAce(index)[-1]) in {
                                profile.sid for profile in profiles
                            }:
                                acl.DeleteAce(index)
                        security.SetNamedSecurityInfo(
                            str(target), security.SE_FILE_OBJECT, 4, None, None, acl, None
                        )
                        verified = security.GetNamedSecurityInfo(
                            str(target), security.SE_FILE_OBJECT, 4
                        )
                        current = verified.GetSecurityDescriptorDacl()
                        if any(
                            security.ConvertSidToStringSid(current.GetAce(i)[-1])
                            in {profile.sid for profile in profiles}
                            for i in range(current.GetAceCount())
                        ):
                            raise RuntimeError("Fixture cleanup could not be verified")
                    report["cleanup"].append(directory)
                    save()
                for index, profile in enumerate(profiles):
                    if profile.sid:
                        profile.delete()
                        report["profiles"][index]["deleted"] = True
                        save()
            except BaseException as exc:
                report["cleanup_error"] = str(exc)
        save()
    expected_checks = {
        "identity_0",
        "identity_1",
        "distinct_identities",
        "overlap",
        "cancel",
        "timeout",
        "host_crash",
        "network_descendants",
        "startup_assign",
        "startup_token",
        "reject_alias_grants",
        "path_aliases",
        "alias_target_unchanged",
    }
    report["passed"] = (
        set(report["checks"]) == expected_checks
        and all(report["checks"].values())
        and not any(report.get(key) for key in ("error", "cleanup_error"))
    )
    save()
    print(
        json.dumps(
            {
                "checks": report["checks"],
                "passed": report["passed"],
                "error": report.get("error"),
                "cleanup_error": report.get("cleanup_error"),
            }
        )
    )
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
