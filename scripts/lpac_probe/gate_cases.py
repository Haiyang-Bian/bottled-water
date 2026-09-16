"""Additional P1 gates in the quiescent probe's new, caller-owned fixture."""

from concurrent.futures import ThreadPoolExecutor
import json
import time

from .jobs import OwnedJob
from .native import LpacProfile
from .standing import environment
from .toolchain import good


def parallel_policies(old, new_preparation, profiles, root, runtime, output, backend, report, save):
    """Overlapping grants must not turn two different tokens into a permission union."""
    narrow = new_preparation("read", 10)
    preparations = (old, narrow)
    indices = (0, 4)
    leases = [item.acquire("parallel-" + str(i)) for i, item in enumerate(preparations)]
    outer, jobs = OwnedJob(), [OwnedJob(), OwnedJob()]
    results = []
    try:
        for index in indices:
            (output / f"Scratch{index}/private.txt").write_text("run-" + str(index))

        def invoke(i):
            index = indices[i]
            own, other = (output / f"Scratch{n}" for n in (index, indices[1 - i]))
            return profiles[index].run(
                [str(runtime / "python/python.exe"), "-I", "-S", "-B",
                 str(runtime / "policy-payload.py"), "--root", str(root), "--own", str(own),
                 "--other", str(other), "--label", f"parallel-{index}"], own,
                environment=environment(own), registry_read=True,
                policy_experiment=preparations[i].generation,
                parent_jobs=(outer.handle, jobs[i].handle))

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(invoke, i) for i in range(2)]
            results = [future.result() for future in futures]
        items = [json.loads(result["stdout"]) for result in results]
        sids = [backend.records[item.generation]["sid"] for item in preparations]
        passed = []
        for i, (result, data) in enumerate(zip(results, items)):
            forbidden = ["write_Archive", "read_Private", "write_Private", "other"]
            if i:
                forbidden.extend(("write_Work", "write_special"))
            passed.append(good(result, sids[i]) and result["verified_job_depth"] == 3
                          and sids[1 - i] not in result["token"]["capabilities"]
                          and data["read_Work"] == {"allowed": True, "value": "work"}
                          and data["read_Archive"] == {"allowed": True, "value": "archive"}
                          and data["own"] == {"allowed": True, "value": "run-" + str(indices[i])}
                          and data["read_special"] == {"allowed": True, "value": "special"}
                          and all(data[key].get("allowed") is False
                                  and data[key].get("errno") == 13 for key in forbidden)
                          and (i == 1 or all(data[key] == {"allowed": True, "value": 7}
                                            for key in ("write_Work", "write_special"))))
        windows = [data["window"] for data in items]
        report["extra_results"]["parallel_policies"] = results
        report["extra_checks"]["parallel_policies"] = all(passed)
        report["extra_checks"]["parallel_overlap"] = (
            max(v[0] for v in windows) < min(v[1] for v in windows))
        report["extra_checks"]["nested_jobs"] = all(j.active() == 0 for j in [outer, *jobs])
    finally:
        for job in [*jobs, outer]:
            job.close()
        for i, lease in enumerate(leases):
            lease.finish(job_drained=not profiles[indices[i]].cleanup_blocked)
        narrow.retire()
        save()


def mutation_gates(new_preparation, backend, root, runtime, output, report, save):
    from agent_contracts.errors import OperationError

    original = (runtime / "hold.py").read_bytes()
    for i, change in enumerate(("modify", "missing", "extra")):
        prepared = new_preparation("read", 20 + i)
        held = output / ("dependency-held-" + change)
        extra = runtime / "unexpected.dll"
        try:
            if change == "modify":
                (runtime / "hold.py").write_bytes(b"changed")
            elif change == "missing":
                (runtime / "hold.py").rename(held)
            else:
                extra.write_bytes(b"unapproved")
            try:
                prepared.acquire("must-not-launch")
            except OperationError as exc:
                report["extra_checks"]["dependency_" + change] = (
                    exc.code == "dependency_changed" and prepared.state == "repair_required"
                    and not prepared.active_runs)
            else:
                raise RuntimeError("Changed dependency was accepted")
        finally:
            if change == "modify":
                (runtime / "hold.py").write_bytes(original)
            elif change == "missing":
                held.rename(runtime / "hold.py")
            else:
                extra.unlink()
            prepared.retire()
            save()

    prepared = new_preparation("read", 24)
    displaced = root / "DisplacedWork"
    (root / "Work").rename(displaced)
    (root / "Work").mkdir()
    try:
        try:
            prepared.acquire("replaced-root")
        except RuntimeError:
            report["extra_checks"]["root_replacement"] = prepared.state == "repair_required"
        else:
            raise RuntimeError("Replacement root was accepted")
        try:
            prepared.retire()
        except RuntimeError:
            report["extra_checks"]["root_cleanup_refused"] = prepared.state == "repair_required"
        else:
            raise RuntimeError("Guessed root cleanup was accepted")
    finally:
        # These two exact objects were created/moved above, within a fresh owned fixture.
        (root / "Work").rmdir()
        displaced.rename(root / "Work")
        prepared.retire()
        report["extra_checks"]["root_repair_verified"] = prepared.state == "retired"
        save()


def command_groups(old, profiles, root, runtime, output, backend, report, save):
    """Two command groups with the same Package SID; timeout must leave its peer alive."""
    import win32api
    import win32event

    outer, run_job = OwnedJob(), OwnedJob()
    original = profiles[0]
    peer = LpacProfile(original.name)
    peer.sid = original.sid
    own = output / "Scratch0"
    first, second = own / "command-a", own / "command-b"
    first.mkdir()
    second.mkdir()
    lease = old.acquire("command-groups")
    handles = []

    def launch(profile, cwd, timeout):
        return profile.run(
            [str(runtime / "python/python.exe"), "-I", "-S", "-B",
             str(runtime / "isolation-payload.py"), "--own", str(cwd), "--sleep"], cwd,
            environment=environment(cwd), registry_read=True,
            policy_experiment=old.generation, parent_jobs=(outer.handle, run_job.handle),
            timeout=timeout)

    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            a, b = pool.submit(launch, original, first, 3), pool.submit(launch, peer, second, 20)
            try:
                deadline = time.monotonic() + 2.5
                while not all((p / "pids.json").is_file() for p in (first, second)):
                    if time.monotonic() > deadline or a.done() or b.done():
                        raise RuntimeError("Concurrent command children did not become ready")
                    time.sleep(0.01)
                for p in (first, second):
                    handles.append([win32api.OpenProcess(0x100000 | 0x1000, False, pid)
                                    for pid in json.loads((p / "pids.json").read_text())])
                first_result = a.result(timeout=8)
                first_dead = all(win32event.WaitForSingleObject(h, 1000) == 0 for h in handles[0])
                second_alive = all(win32event.WaitForSingleObject(h, 0) == 258 for h in handles[1])
                report["extra_checks"]["command_timeout_isolated"] = (
                    first_result["timed_out"] and first_result["job_drained"] and first_dead
                    and second_alive and not b.done())
            finally:
                run_job.terminate()
            second_result = b.result(timeout=8)
            report["extra_results"]["command_groups"] = [first_result, second_result]
            report["extra_checks"]["run_job_terminates_all"] = (
                second_result["job_drained"] and all(
                    win32event.WaitForSingleObject(h, 1000) == 0 for group in handles for h in group))
    finally:
        run_job.close()
        outer.close()
        for group in handles:
            for handle in group:
                handle.Close()
        original.cleanup_blocked |= peer.cleanup_blocked
        lease.finish(job_drained=not original.cleanup_blocked)
        save()
