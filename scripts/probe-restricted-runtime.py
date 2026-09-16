"""Deterministic Provider -> real Runtime -> real LPAC workers, in a fresh fixture."""

import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import asdict
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
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
from agent_contracts.permissions import PathPermission, StandingPermissionPolicy
from agent_contracts.resources import ResourceRevision, SoftwareSpec
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.permission_preparation import PreparedPolicy
from agent_subsystems.workspaces.permissions import freeze_policy
from lpac_probe.quiescent import FixturePreparationBackend
from lpac_probe.runtime_fixture import block_host_business_io, bundle
from model_provider.core.interfaces import BaseModelProvider, StreamChunk


class FixtureProvider(BaseModelProvider):
    def __init__(self, root, software_ids, resource_id, paths, *, full=False,
                 powershell=False, uv=False):
        super().__init__({"model": "restricted-deterministic"})
        self.root, self.software_ids, self.full = root, software_ids, full
        self.resource_id, self.paths = resource_id, paths
        self.powershell = powershell or full
        self.uv = uv or full
        self.steps, self.results = 0, []
        self.expected = True

    async def chat(self, *args, **kwargs):
        raise AssertionError("Streaming expected")

    async def chat_stream(self, messages, **kwargs):
        previous = next((m for m in reversed(messages) if m.role == "tool"), None)
        if previous:
            value = json.loads(previous.content)
            self.results.append(value)
            assert value["success"] is self.expected, value
        calls = [
            ("file.read", {"path": str(self.root / "Archive/sample.txt")}),
            ("file.read", {"path": "calc.py"}),
            ("file.edit", {"path": "calc.py", "old_text": "a - b", "new_text": "a + b",
                           "expected_hash": self.results[-1]["result"].get("sha256", "")
                           if self.results else ""}),
            ("file.read", {"path": str(self.root / "Private/sample.txt")}),
            ("software.run", {"id": self.software_ids["python"], "args": ["-B", "-c",
                "from pathlib import Path; from calc import add; assert add(2,3)==5; "
                "Path('output.txt').write_text('5'); print('TEST PASSED')"], "outputs": ["output.txt"]}),
            ("file.list", {"path": "."}),
            ("file.search", {"query": "a + b"}),
            ("resource.verify", {"id": self.resource_id}),
            ("software.run", {"id": self.software_ids["python"], "args": ["-B", "-c",
                "from pathlib import Path\n"
                f"root=Path({str(self.root)!r})\n"
                "assert (root/'Archive/sample.txt').read_text()=='Archive'\n"
                "for p, mode in [(root/'Private/sample.txt','r'),"
                "(root/'Archive/sample.txt','w')]:\n"
                " try:\n  p.open(mode).close()\n"
                " except PermissionError as e:\n  print('OS DENIED',e.winerror)\n"
                " else:\n  raise AssertionError('OS permission bypass')\n"]}),
            ("file.write", {"path": "中文 路径.txt", "content": "中文\n", "expected_hash": "new"}),
            ("file.read", {"path": "中文 路径.txt"}),
            ("file.edit", {"path": "calc.py", "old_text": "a + b", "new_text": "a * b",
                           "expected_hash": "0" * 64}),
        ]
        if self.powershell:
            python = self.paths["python"].replace("'", "''")
            calls.extend([
                ("powershell.run", {"script": "Get-Content calc.py | Select-Object -First 1"}),
                ("powershell.run", {"cwd": "子 ' 目录", "script":
                    "Get-Content -LiteralPath 'location.txt';\n"
                    f"& '{python}' -B -c \"from pathlib import Path; "
                    "assert Path('location.txt').read_text()=='PS CWD OK'; print('CHILD CWD OK')\""}),
            ])
        if self.full:
            calls.append(("git.run", {"args": ["diff", "--", "calc.py"]}))
        if self.uv:
            calls.append(
                ("software.run", {"id": self.software_ids["uv"], "args": ["run",
                    "--offline", "--no-project",
                    "--", "python", "-B", "-c", "from calc import add; assert add(2,3)==5; print('UV OK')"]}),
            )
        if self.steps < len(calls):
            name, args = calls[self.steps]
            self.expected = self.steps not in {3, 11}
            yield StreamChunk(tool_call={"id": f"call-{self.steps}", "index": 0, "type": "function",
                "function": {"name": name, "arguments": json.dumps(args)}})
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(content='Task verified.\n```status_report\n{"state":"completed",'
                              '"will":"complete"}\n```', finish_reason="stop")
        self.steps += 1
        yield StreamChunk(usage={"prompt_tokens": 20, "completion_tokens": 10})


async def run(args, repo, output, report, save):
    root, runtime = output / "Tree", output / "Runtime"
    for name in ("Work", "Archive", "Private"):
        (root / name).mkdir(parents=True)
        (root / name / "sample.txt").write_text(name, encoding="utf-8")
    (root / "Work/calc.py").write_bytes(b"\xef\xbb\xbfdef add(a, b):\r\n    return a - b\r\n")
    (root / "Work/子 ' 目录").mkdir()
    (root / "Work/子 ' 目录/location.txt").write_text("PS CWD OK", encoding="utf-8")
    paths = bundle(repo, runtime)
    if args.namespace_experiment:
        from lpac_probe.toolchain import prepare

        prepare(runtime, root)
        paths.update(pwsh=str(runtime / "pwsh/pwsh.exe"), git=str(runtime / "git/git.exe"),
                     uv=str(runtime / "uv.exe"))
        subprocess.run([paths["git"], "-C", str(root / "Work"), "add", "calc.py"], check=True,
                       capture_output=True, timeout=10)
    elif args.powershell:
        from lpac_probe.toolchain import prepare_powershell

        prepare_powershell(runtime)
        paths["pwsh"] = str(runtime / "pwsh/pwsh.exe")
    if args.uv and "uv" not in paths:
        source = shutil.which("uv.exe")
        if not source:
            raise RuntimeError("An explicit uv installation is required")
        shutil.copyfile(source, runtime / "uv.exe")
        paths["uv"] = str(runtime / "uv.exe")
    (output / "Runs").mkdir()
    store = SQLiteStore(output / "state.sqlite3")
    cwd = Path(os.path.normcase(str(root / "Work")))
    session = store.new_session(cwd)
    snapshot = freeze_policy(StandingPermissionPolicy(store.environment.environment_id, "local", 1,
        (PathPermission(cwd, "modify"), PathPermission(root / "Archive", "read")), enabled=True))
    manifest = DependencyManifest.capture(runtime)
    backend = FixturePreparationBackend(root, report, save, dependencies=manifest)
    prepared = PreparedPolicy(snapshot, manifest.digest, backend)
    driver = model = None
    enabled, violations = [False], []
    try:
        prepared.prepare()
        driver = WindowsRestrictedDriver(prepared, runtime, output / "Runs", paths,
                                         namespace_experiment=args.namespace_experiment)
        original_prepare = driver.prepare

        async def prepare_execution(context):
            await original_prepare(context)
            if not getattr(driver.profile, "diagnostic_wrapped", False):
                original_run = driver.profile.run

                def trace_transport(*argv, **kwargs):
                    result = original_run(*argv, **kwargs)
                    if result["exit_code"] != 0 or result["pipe_errors"]:
                        report.setdefault("worker_diagnostics", []).append({
                            key: result[key] for key in ("exit_code", "stderr", "pipe_errors")})
                        save()
                    return result

                driver.profile.run = trace_transport
                driver.profile.diagnostic_wrapped = True
            enabled[0] = True

        driver.prepare = prepare_execution
        catalog = SQLiteResources(store)
        access = catalog.access()
        software_ids = {}
        for kind in ("python", "uv", "git"):
            if kind not in paths:
                continue
            software = catalog.save(access, ResourceRevision("Fixture " + kind, paths[kind], "software"))
            catalog.save_software(access, software.id, software.revision, SoftwareSpec(software.id,
                kind, software.content.path, driver.software[kind]["sha256"], "fixture",
                "2026-09-16T00:00:00+00:00", True))
            software_ids[kind] = software.id
        resource = catalog.save(access, ResourceRevision("Archive reference",
                                                        str(root / "Archive/sample.txt")))
        grant = ResourceGrant(WorkspaceSpec(tuple(rule.path for rule in snapshot.policy.grants)),
                              frozenset({"files", "process"}), "windows_lpac", snapshot)
        execution = ExecutionBindings(grant, ExecutionLocation(cwd), driver, driver,
            RestrictedAuthorization(), RestrictedSoftware(driver, {
                identifier: kind for kind, identifier in software_ids.items()}), paths,
            {"driver": "windows_lpac", "policy_digest": snapshot.digest,
             "dependency_digest": manifest.digest, "network": "deny"})
        redactor, profile, config = Redactor(), Profile("deepseek", "fixture", "env:UNUSED"), {}
        prompt = "Read Archive, fix calc, run tests; Private must stay denied."
        if args.profile:
            from agent_cli.config import load_config, select_profile
            from agent_cli.provider import LocalModelProvider
            from agent_adapters.credentials.local import LocalCredentialStore
            from model_provider import create_provider

            config = load_config(args.source_home)
            name, profile = select_profile(config, args.profile)
            secret = LocalCredentialStore(args.source_home / "credentials").resolve(profile.credential_ref)
            redactor = Redactor([secret])
            args.redactor = redactor
            model = LocalModelProvider(create_provider({**asdict(profile), "api_key": secret}), redactor)
            report["provider"] = {"profile": name, "provider": profile.provider, "model": profile.model}
            config = {"active_profile": name, "execution": {"max_model_turns": 35},
                      "limits": {"wall_time_seconds": 420}}
            prompt = (
                "这是隔离夹具的实际验收，请执行工具，不要只提供建议。读取 Archive/sample.txt，"
                "读取 Work/calc.py 并修复 add(2,3) 应为5的问题，用已登记 Python 执行离线断言测试，"
                "将结果5写入 Work/output.txt 并声明该产物供前后检查。不要改动BOM及CRLF。"
                "用 file.read 尝试读取 Private/sample.txt，拒绝后继续，不改变授权。"
                "再用已登记 Python 尝试打开该 Private 文件，捕获 PermissionError并输出OS拒绝。"
                "用resource.verify核实Archive资源，用PowerShell 7读取修改的代码，"
                "用git.run查看diff，用已登记uv离线运行同样断言。完成后据实报告。"
                f"\n夹具根目录：{root}。当前目录：{cwd}。"
                "\nWork 本身就是 Git 仓库；默认 cwd 已是 Work，calc.py 直接用相对路径。"
                "不要把 cwd 设为夹具根目录，该父目录未授权。Archive/Private 请用上面根目录下的绝对路径。"
                f"\n软件ID：{json.dumps(software_ids)}；Archive资源ID：{resource.id}。"
            )
        else:
            model = FixtureProvider(root, software_ids, resource.id, paths,
                                    full=bool(args.namespace_experiment), powershell=args.powershell,
                                    uv=args.uv)
        with (output / "events.jsonl").open("w", encoding="utf-8") as events:
            with redirect_stdout(events), block_host_business_io(root, enabled, violations):
                code = await run_turn(store, session, model, profile, config, redactor, prompt,
                    json_mode=True, execution=execution)
        enabled[0] = False
        saved = store.db.execute("SELECT id FROM runs WHERE scope=?", (session["id"],)).fetchone()[0]
        result = store.run_result(saved)
        events = [json.loads(line) for line in (output / "events.jsonl").read_text(encoding="utf-8").splitlines()]
        tool_results = [event["payload"] for event in events if event.get("type") == "agent.tool_result"]
        report.update(exit_code=code, run_id=saved, result=result, host_bypasses=violations,
                      tool_results=tool_results, policy_digest=snapshot.digest,
                      dependency_digest=manifest.digest)
        report["checks"] = {
            "denial": any(not t["success"] and (t.get("result") or {}).get("error_code") == "permission_denied"
                          for t in tool_results),
            "resource_probe": any(t["tool"] == "resource.verify" and t["success"] for t in tool_results),
            "python_output": any(t["tool"] == "software.run" and t["success"]
                and (t.get("result") or {}).get("outputs") for t in tool_results),
            "single_terminal": sum(e.get("type") in {"system.run_completed", "system.run_failed",
                                                       "system.run_cancelled"} for e in events) == 1,
        }
        if args.namespace_experiment or args.powershell:
            report["checks"]["powershell"] = any(t["tool"] == "powershell.run" and t["success"]
                and "def add(a, b):" in t["result"].get("stdout", "") for t in tool_results)
            if not args.profile:
                report["checks"]["powershell_child_cwd"] = any(
                    t["tool"] == "powershell.run" and t["success"]
                    and "CHILD CWD OK" in t["result"].get("stdout", "") for t in tool_results)
        if args.namespace_experiment:
            report["checks"]["git"] = any(t["tool"] == "git.run" and t["success"] for t in tool_results)
        if args.namespace_experiment or args.uv:
            report["checks"]["uv"] = any(t["tool"] == "software.run" and t["success"]
                and (t.get("result") or {}).get("software_id") == software_ids["uv"] for t in tool_results)
        report["passed"] = (code == 0 and not violations
            and all(report["checks"].values())
            and (bool(args.profile) or len(model.results) >= 12)
            and (root / "Work/output.txt").read_text().strip() == "5"
            and (root / "Work/calc.py").read_bytes() == b"\xef\xbb\xbfdef add(a, b):\r\n    return a + b\r\n"
            and (root / "Private/sample.txt").read_text() == "Private")
    finally:
        enabled[0] = False
        if model:
            await model.aclose()
        if driver and not driver.closed:
            await driver.aclose()
        prepared.retire()
        report["cleanup_verified"] = prepared.state == "retired" and (not driver or driver.closed)
        store.close()
        save()


def print_summary(output, report, redactor):
    # Pipe encodings on Windows can be GBK even when report files are UTF-8.
    # ASCII JSON preserves all text through escapes after credential redaction.
    value = json.loads(redactor.dumps({"report": str(output / "report.json"),
        "passed": report["passed"], "error": report.get("error"), "result": report.get("result")}))
    print(json.dumps(value, ensure_ascii=True, separators=(",", ":")))
    return 0 if report["passed"] and report.get("cleanup_verified") else 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--namespace-experiment")
    parser.add_argument("--powershell", action="store_true",
                        help="Add PowerShell cwd and private-cache cleanup checks without UAC")
    parser.add_argument("--uv", action="store_true",
                        help="Add isolated uv discovery checks without UAC")
    parser.add_argument("--source-home", type=Path)
    parser.add_argument("--profile")
    args = parser.parse_args()
    args.redactor = Redactor()
    if bool(args.profile) != bool(args.source_home):
        parser.error("Live calls require both explicit --profile and --source-home")
    repo, output = Path(__file__).resolve().parents[1], args.output.resolve()
    if ctypes.windll.shell32.IsUserAnAdmin() or output.parent != repo / "var" or output.exists():
        parser.error("Ordinary user and fresh repository var output required")
    output.mkdir()
    report = {"passed": False, "source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip(),
        "os_build": str(sys.getwindowsversion()), "not_executed": [] if args.namespace_experiment else
        ["full_toolchain_requires_initialization"]}
    report["source_files"] = {str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
        for folder in (repo / "src", repo / "scripts") for path in sorted(folder.rglob("*.py"))}

    def save():
        (output / "report.json").write_text(args.redactor.dumps(report), encoding="utf-8")

    try:
        asyncio.run(run(args, repo, output, report, save))
    except BaseException as exc:
        report.update(error=str(exc), traceback=traceback.format_exc())
        report["passed"] = False
    save()
    return print_summary(output, report, args.redactor)


if __name__ == "__main__":
    raise SystemExit(main())
