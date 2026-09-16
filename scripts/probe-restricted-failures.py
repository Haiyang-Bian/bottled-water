"""Real LPAC command groups through run_turn, plus explicitly labelled fault injection."""

import argparse
import asyncio
from contextlib import redirect_stdout
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import traceback

from agent_adapters.local.dependencies import DependencyManifest
from agent_adapters.local.restricted import RestrictedAuthorization, WindowsRestrictedDriver
from agent_adapters.local.restricted_software import RestrictedSoftware
from agent_adapters.storage.resources import SQLiteResources
from agent_adapters.storage.sqlite import SQLiteStore
from agent_cli.config import Profile
from agent_cli.execution_bindings import ExecutionBindings
from agent_cli.host import run_turn
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_contracts.harness import ExecutionStopped
from agent_contracts.permissions import PathPermission, StandingPermissionPolicy
from agent_contracts.resources import ResourceRevision, SoftwareSpec
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.permission_preparation import PreparedPolicy
from agent_subsystems.workspaces.permissions import freeze_policy
from lpac_probe.quiescent import FixturePreparationBackend
from lpac_probe.runtime_fixture import bundle
from model_provider.core.interfaces import BaseModelProvider, StreamChunk


class FailureProvider(BaseModelProvider):
    def __init__(self, case, software, marker):
        super().__init__({"model": "deterministic-faults"})
        self.case, self.software, self.marker = case, software, marker
        self.calls, self.results = 0, []
        self.returning = asyncio.Event()

    async def chat(self, *args, **kwargs):
        raise AssertionError("Streaming expected")

    async def chat_stream(self, messages, **kwargs):
        self.calls += 1
        if self.calls == 1:
            if self.case in {"timeout", "cancel", "lease"}:
                child = "import time; time.sleep(60)"
                code = ("import os,sys,subprocess,time\nfrom pathlib import Path\n"
                    f"p=subprocess.Popen([sys.executable,'-I','-S','-c',{child!r}])\n"
                    f"Path({self.marker.name!r}).write_text(str(os.getpid())+','+str(p.pid))\n"
                    "time.sleep(60)\n")
                tool, args = "software.run", {"id": self.software, "args": ["-I", "-S", "-c", code],
                    "timeout": 1.5 if self.case == "timeout" else 45}
            else:
                tool, args = "file.read", {"path": "sample.txt"}
            yield StreamChunk(tool_call={"id": "fault-call", "index": 0, "type": "function",
                "function": {"name": tool, "arguments": json.dumps(args)}})
            yield StreamChunk(finish_reason="tool_calls")
        else:
            previous = next(m for m in reversed(messages) if m.role == "tool")
            self.results.append(json.loads(previous.content))
            self.returning.set()
            if self.case == "lease_model_race":
                await asyncio.sleep(0.5)
            yield StreamChunk(content='Verified fault.\n```status_report\n{"state":"completed",'
                                      '"will":"complete"}\n```', finish_reason="stop")
        yield StreamChunk(usage={"prompt_tokens": 10, "completion_tokens": 5})


def exited(pid):
    import win32api
    import win32event

    try:
        handle = win32api.OpenProcess(0x100000, False, pid)
    except Exception as exc:
        if getattr(exc, "winerror", None) == 87:
            return True
        raise
    try:
        return win32event.WaitForSingleObject(handle, 0) == 0
    finally:
        handle.Close()


async def run(repo, output, report, save):
    tree, runtime = output / "Tree", output / "Runtime"
    work = tree / "Work"
    work.mkdir(parents=True)
    (work / "sample.txt").write_text("safe")
    (output / "Runs").mkdir()
    paths = bundle(repo, runtime)
    store = SQLiteStore(output / "state.sqlite3")
    cwd = Path(os.path.normcase(str(work)))
    snapshot = freeze_policy(StandingPermissionPolicy(store.environment.environment_id, "local", 1,
        (PathPermission(cwd, "modify"),), enabled=True))
    manifest = DependencyManifest.capture(runtime)
    prepared = PreparedPolicy(snapshot, manifest.digest,
        FixturePreparationBackend(tree, report, save, dependencies=manifest))
    prepared.prepare()
    catalog = SQLiteResources(store)
    software = catalog.save(catalog.access(), ResourceRevision("Python", paths["python"], "software"))
    try:
        for case in ("timeout", "cancel", "lease", "protocol", "worker_exit", "cleanup", "lease_model_race"):
            session = store.new_session(cwd)
            driver = WindowsRestrictedDriver(prepared, runtime, output / "Runs", paths)
            software = catalog.read(catalog.access(), software.id)
            catalog.save_software(catalog.access(), software.id, software.revision, SoftwareSpec(software.id,
                "python", software.content.path, driver.software["python"]["sha256"], "fixture",
                "2026-09-16T00:00:00+00:00", True))
            model = FailureProvider(case, software.id, work / (case + "-pids.txt"))
            binding = ExecutionBindings(ResourceGrant(WorkspaceSpec((cwd,)), frozenset({"files", "process"}),
                "windows_lpac", snapshot), ExecutionLocation(cwd), driver, driver,
                RestrictedAuthorization(), RestrictedSoftware(driver, {software.id: "python"}), paths, {})
            original_prepare = driver.prepare

            async def prepare(context):
                await original_prepare(context)
                if case in {"protocol", "worker_exit"} and not getattr(driver, "injected", False):
                    original_run = driver.profile.run

                    def transport(argv, *a, **kw):
                        if case == "worker_exit":
                            argv = [paths["python"], "-I", "-S", "-c", "raise SystemExit(77)"]
                        result = original_run(argv, *a, **kw)
                        if case == "protocol":
                            result["stdout"] = b"corrupt frame"
                        return result

                    driver.profile.run = transport
                    driver.injected = True

            driver.prepare = prepare
            if case == "cleanup":
                original_drain = driver.drain

                async def uncertain_drain():
                    await original_drain()
                    raise ExecutionStopped("isolation_cleanup_unconfirmed")

                driver.drain = uncertain_drain

            async def interrupt():
                if case == "lease_model_race":
                    await asyncio.wait_for(model.returning.wait(), 30)
                else:
                    async with asyncio.timeout(30):
                        while not model.marker.exists():
                            await asyncio.sleep(0.025)
                if case == "cancel":
                    signal.raise_signal(signal.SIGINT)
                else:
                    driver.invalid_reason = "permission_lease_invalid"

            interrupter = asyncio.create_task(interrupt()) if case in {"cancel", "lease", "lease_model_race"} else None
            try:
                with (output / (case + ".jsonl")).open("w", encoding="utf-8") as stream, redirect_stdout(stream):
                    async with asyncio.timeout(60):
                        code = await run_turn(store, session, model, Profile("deepseek", "fixture", "env:NONE"),
                            {}, Redactor(), "Execute this fault fixture", json_mode=True, execution=binding)
                if interrupter:
                    await interrupter
                run_id = store.db.execute("SELECT id FROM runs WHERE scope=?", (session["id"],)).fetchone()[0]
                result = store.run_result(run_id)
                events = [json.loads(line) for line in (output / (case + ".jsonl")).read_text().splitlines()]
                terminal = [e for e in events if e.get("type") in
                            {"system.run_completed", "system.run_failed", "system.run_cancelled"}]
                expected = {"timeout": (0, "completed"), "cancel": (130, "user_cancelled"),
                    "lease": (1, "permission_lease_invalid"), "protocol": (1, "worker_protocol_error"),
                    "worker_exit": (1, "worker_protocol_error"), "cleanup": (1, "isolation_cleanup_unconfirmed"),
                    "lease_model_race": (1, "permission_lease_invalid")}[case]
                pids = [int(v) for v in model.marker.read_text().split(",")] if model.marker.exists() else []
                checks = {"status": (code, result["reason_code"]) == expected, "terminal": len(terminal) == 1,
                    "context": result["context_version"] == (1 if case == "timeout" else 0),
                    "descendants": all(exited(pid) for pid in pids), "closed": driver.closed,
                    "lease_finished": not prepared.active_runs}
                if case in {"timeout", "cancel", "lease"}:
                    checks["children_started"] = len(pids) == 2
                if case == "timeout":
                    checks["recoverable_timeout"] = (model.results[0]["success"] is False
                        and model.results[0]["result"]["error_code"] == "process_timeout")
                report.setdefault("cases", {})[case] = {"run_id": run_id, "result": result, "exit_code": code,
                    "checks": checks, "pids": pids, "fault_injection": case in {"protocol", "worker_exit", "cleanup"}}
                save()
                if not all(checks.values()):
                    raise AssertionError(f"{case}: {checks}")
            finally:
                if interrupter:
                    interrupter.cancel()
                    await asyncio.gather(interrupter, return_exceptions=True)
                await driver.aclose()
    finally:
        prepared.retire()
        report["cleanup_verified"] = prepared.state == "retired"
        store.close()
        save()
    report["passed"] = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    repo, output = Path(__file__).resolve().parents[1], args.output.resolve()
    if ctypes.windll.shell32.IsUserAnAdmin() or output.parent != repo / "var" or output.exists():
        parser.error("Ordinary user and fresh repository var output required")
    output.mkdir()
    report = {"passed": False, "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(), "os_build": str(sys.getwindowsversion())}
    report["source_files"] = {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (repo / "src", repo / "scripts") for path in sorted(folder.rglob("*.py"))}

    def save():
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")

    try:
        asyncio.run(run(repo, output, report, save))
    except BaseException:
        report["error"] = traceback.format_exc()
    save()
    print(json.dumps({"report": str(output / "report.json"), "passed": report["passed"], "error": report.get("error")}))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
