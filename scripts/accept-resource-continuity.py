"""Explicit installed-wheel L3 acceptance with real provider and Windows ConPTY.

Only the named profile/credential is read from the source home. All database and
filesystem mutations use an isolated temporary project; evidence is redacted.
"""

import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict

from agent_adapters.credentials.local import LocalCredentialStore
from agent_cli.config import load_config, save_config, select_profile
from agent_subsystems.observability.redaction import Redactor


def main():
    parser = argparse.ArgumentParser()
    for name in ("source-home", "python", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--profile", required=True)
    args = parser.parse_args()
    config = load_config(args.source_home)
    _, profile = select_profile(config, args.profile)
    secret = LocalCredentialStore(args.source_home / "credentials").resolve(profile.credential_ref)
    redactor = Redactor([secret])
    python, output = str(args.python.resolve()), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = {
        "status": "running",
        "provider": profile.provider,
        "model": profile.model,
        "profile": args.profile,
        "checks": [],
        "runs": [],
        "configured_providers": sorted({p["provider"] for p in config["profiles"].values()}),
    }
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="agenthub-l3-live-") as temporary:
            base = Path(temporary)
            home, a, b = base / "home", base / "甲 A", base / "乙 B"
            a.mkdir()
            b.mkdir()
            (a / "experiment.py").write_text("def square(x):\n    return x + x\n", encoding="utf-8")
            (a / "test_experiment.py").write_text(
                "import unittest\nfrom experiment import square\nclass Check(unittest.TestCase):\n"
                "    def test_square(self):\n        self.assertEqual(square(3), 9)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "init", "-q", str(a)], check=True, capture_output=True)
            value = {k: v for k, v in asdict(profile).items() if v is not None}
            value["credential_ref"] = "env:AGENTHUB_L3_LIVE_KEY"
            save_config(home, {"default_profile": "qa", "profiles": {"qa": value}})
            env = {**os.environ, "AGENTHUB_HOME": str(home), "AGENTHUB_L3_LIVE_KEY": secret}
            env.pop("PYTHONPATH", None)

            def snapshot():
                with contextlib.closing(
                    sqlite3.connect((home / "state.sqlite3").as_uri() + "?mode=ro", uri=True)
                ) as db:
                    evidence["runs"] = [
                        {
                            "run_id": rid,
                            "request": json.loads(req),
                            "result": json.loads(result) if result else None,
                        }
                        for rid, req, result in db.execute(
                            "SELECT id,request,result FROM runs ORDER BY created,id"
                        )
                    ]
                    for key, kind in (
                        ("resource_used", "agent.resources_used"),
                        ("tool_results", "agent.tool_result"),
                        ("tool_started", "agent.tool_started"),
                        ("memory_used", "agent.memory_used"),
                    ):
                        evidence[key] = [
                            json.loads(r[0])
                            for r in db.execute(
                                "SELECT body FROM events WHERE json_extract(body,'$.type')=? ORDER BY run,sequence",
                                (kind,),
                            )
                        ]
                    for table in ("resources", "software", "resource_jobs", "memory_jobs"):
                        db.row_factory = sqlite3.Row
                        evidence[table] = [dict(r) for r in db.execute("SELECT * FROM " + table)]

            def cli(name, cwd, *arguments, expected=0):
                result = subprocess.run(
                    [python, "-B", "-m", "agent_cli.main", *arguments],
                    cwd=cwd,
                    env=env,
                    capture_output=True,
                    encoding="utf-8",
                    timeout=360,
                )
                for suffix, text in (("stdout", result.stdout), ("stderr", result.stderr)):
                    (output / f"{name}.{suffix}.txt").write_text(
                        redactor.text(text), encoding="utf-8"
                    )
                evidence["checks"].append({"name": name, "exit_code": result.returncode})
                if (home / "state.sqlite3").exists():
                    snapshot()
                assert result.returncode == expected, redactor.text(result.stdout + result.stderr)
                print(name + ": passed", flush=True)
                return result.stdout

            def model(name, cwd, prompt, *, plain=False):
                cli(name, cwd, "--plain" if plain else "--json", "--profile", "qa", "-p", prompt)
                row = evidence["runs"][-1]
                assert row["result"]["state"] == "completed", row
                return row, [
                    e["payload"] for e in evidence["tool_results"] if e["run_id"] == row["run_id"]
                ]

            evidence["version"] = cli("version", b, "--version").strip()
            cli("trust-a", a, "trust", "add", str(a))
            cli("trust-b", b, "trust", "add", str(b))
            registered = {}
            for kind, executable in (
                ("python", sys._base_executable),
                ("uv", shutil.which("uv")),
                ("git", shutil.which("git")),
            ):
                assert executable
                cli("discover-" + kind, a, "--json", "software", "discover", "--kind", kind)
                registered[kind] = json.loads(
                    cli(
                        "register-" + kind,
                        a,
                        "--json",
                        "software",
                        "add",
                        "--kind",
                        kind,
                        "--path",
                        executable,
                    )
                )
                cli("details-" + kind, a, "--json", "software", "show", registered[kind]["id"])
            pyid = registered["python"]["id"]
            gitid, uvid = registered["git"]["id"], registered["uv"]["id"]
            first, tools = model(
                "01-experiment",
                a,
                "L3实验甲唯一入口。读取 experiment.py 和 test_experiment.py，修正 square 错误，"
                f"用已登记 Python（ID={pyid}）的 software.run 执行 -m unittest -v。"
                "测试通过后再用这个 Python 执行 -c 脚本写 report.json，内容为 square(3) 的计算结果，"
                "software.run 的 outputs 显式提供 report.json。"
                f"用 Git（ID={gitid}）software.run 查看 diff --no-index -- /dev/null experiment.py，"
                "该 diff 有差异时退出 1 是正常的，不修改仓库。"
                f"用 uv（ID={uvid}）software.run 执行 --version。"
                "最后 memory.propose 提出 user_stated 经验候选『实验先执行测试』，来源使用当前 request。"
                "不采纳记忆。报告真实测试和产物内容。",
            )
            assert any(t["tool"] == "software.run" and t["result"].get("outputs") for t in tools)
            assert "9" in (a / "report.json").read_text()
            # Models may select the existing git.run tool despite the initial prompt.
            # Verify each registered executable through software.run, recording extra Runs.
            for kind, registered_record in registered.items():
                if not any(
                    t["result"].get("software_id") == registered_record["id"]
                    for t in tools
                    if isinstance(t.get("result"), dict)
                ):
                    _, extra = model(
                        "01-check-" + kind,
                        a,
                        f"验证已登记软件：只调用 software.run ID={registered_record['id']} "
                        'args=["--version"]，不要用 PowerShell 或 git.run 替代。报告实际版本。',
                    )
                    assert any(
                        t["tool"] == "software.run"
                        and t["success"]
                        and t["result"].get("software_id") == registered_record["id"]
                        for t in extra
                    )
            report = next(
                r
                for r in json.loads(
                    cli("02-resources", b, "--json", "resources", "search", "report")
                )
                if r["content"]["path"].endswith("report.json")
            )
            report = json.loads(
                cli(
                    "03-classify",
                    b,
                    "--json",
                    "resources",
                    "edit",
                    report["id"],
                    "--revision",
                    str(report["revision"]),
                    "--name",
                    "实验报告",
                    "--kind",
                    "artifact",
                    "--alias",
                    "平方结果",
                )
            )
            first_scope = first["result"]["context_scope_id"]
            other, tools = model(
                "04-b-metadata",
                b,
                f"用 resource.read 读取 {report['id']} 的元数据，再用 resource.verify 尝试核实它。"
                "如果目录未授权，只报告拒绝，不用其它工具绕过。"
                f"用 task.read 查看任务 {first_scope} 的摘要，并用 task.search 查找今天的实验。"
                "说明已知资源及来源；这是资料查询任务，不恢复旧任务或打开旧文件。",
                plain=True,
            )
            assert any(
                t["tool"] == "resource.verify"
                and not t["success"]
                and t["result"].get("error_code") == "outside_workspace"
                for t in tools
            )
            assert other["result"]["context_scope_id"] != first_scope
            assert report["id"] in json.dumps(evidence["resource_used"])
            assert not json.loads(
                cli("05-unapproved", b, "--json", "memory", "search", "实验先执行测试")
            )
            terminal_check(python, a, b, env, output, evidence, snapshot, pyid)
            # External relocation and copying are explicit acceptance fixtures, never inferred by the catalog.
            moved = a / "moved.json"
            (a / "report.json").rename(moved)
            missing = json.loads(
                cli(
                    "06-missing",
                    a,
                    "--json",
                    "resources",
                    "verify",
                    report["id"],
                    "--revision",
                    str(report["revision"]),
                )
            )
            assert missing["observation"]["facts"]["exists"] is False
            moved_record = json.loads(
                cli(
                    "07-relocate",
                    a,
                    "--json",
                    "resources",
                    "relocate",
                    report["id"],
                    "--revision",
                    str(report["revision"]),
                    "--path",
                    str(moved),
                )
            )
            checked = json.loads(
                cli(
                    "08-verify",
                    a,
                    "--json",
                    "resources",
                    "verify",
                    report["id"],
                    "--revision",
                    str(moved_record["revision"]),
                )
            )
            assert checked["observation"]["facts"]["exists"]
            shutil.copyfile(moved, a / "copy.json")
            copied = json.loads(
                cli(
                    "09-copy",
                    a,
                    "--json",
                    "resources",
                    "add",
                    "--name",
                    "独立副本",
                    "--path",
                    "copy.json",
                    "--kind",
                    "artifact",
                )
            )
            assert copied["id"] != report["id"]
            fixture = a / "uv-fixture.exe"
            shutil.copyfile(shutil.which("uv"), fixture)
            fixture_record = json.loads(
                cli(
                    "10-fixture-register",
                    a,
                    "--json",
                    "software",
                    "add",
                    "--kind",
                    "uv",
                    "--path",
                    str(fixture),
                )
            )
            with fixture.open("ab") as stream:
                stream.write(b"changed-fixture")
            _, tools = model(
                "11-changed-executable",
                a,
                f'调用 software.run，ID={fixture_record["id"]}，args=["--version"]。'
                "如果返回 software_changed，报告用户需重新验证即可，不执行其它程序、不尝试修复。",
            )
            assert any(
                t["tool"] == "software.run" and t["result"].get("error_code") == "software_changed"
                for t in tools
            )
            cli("12-process", b, "--json", "resources", "process")
            cli("13-rebuild", b, "--json", "resources", "rebuild")
            evidence["status"] = "passed"
    except BaseException as exc:
        evidence["status"], evidence["error"] = "failed", redactor.text(str(exc))
        raise
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "acceptance.json").write_text(redactor.dumps(evidence), encoding="utf-8")


def terminal_check(python, a, b, env, output, evidence, snapshot, pyid):
    spec = importlib.util.spec_from_file_location(
        "terminal_qa", Path(__file__).with_name("accept-cli-terminal.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    terminal = module.Terminal(
        [python, "-B", "-m", "agent_cli.main", "--profile", "qa", "resume", "--query", "实验"],
        b,
        env,
        output,
    )
    try:
        terminal.wait("选择任务")
        terminal.snapshot("14-global-selector")
        mark = terminal.send("L3实验甲唯一入口\r")
        terminal.wait("agenthub>", after=mark)
        terminal.snapshot("15-restored-a")
        mark = terminal.send(
            f'继续实验。用 resource.search 找 report.json，然后 resource.verify 核实。再用 software.run 的 Python ID={pyid}，执行 -c 脚本读取 report.json 并写 followup.txt 为字符串 continued，outputs=["followup.txt"]，报告实际结果。\r'
        )
        terminal.wait("Run:", after=mark, timeout=300)
        terminal.wait("agenthub>", after=terminal.raw.rfind("Run:"))
        assert (a / "followup.txt").read_text() == "continued" and not (b / "followup.txt").exists()
        terminal.snapshot("16-continued-location-a")
        mark = terminal.send("/resources\r")
        terminal.wait("资源 · 元数据不授予文件访问权", after=mark)
        terminal.snapshot("17-resource-selector")
        terminal.send("\x1b")
        terminal.wait("agenthub>", after=mark)
        terminal.proc.setwinsize(24, 75)
        terminal.screen.resize(lines=24, columns=75)
        mark = terminal.send("/software\r")
        terminal.wait("资源 · 元数据不授予文件访问权", after=mark)
        terminal.snapshot("18-software-narrow")
        terminal.send("\x1b")
        terminal.wait("agenthub>", after=mark)
        mark = terminal.send(
            f'调用 software.run ID={pyid} args=["-c","import os,time; from pathlib import Path; Path(\'cancel-pid.txt\').write_text(str(os.getpid())); time.sleep(120)"]，outputs=["cancel-missing.txt"]，timeout=120。\r'
        )
        deadline = time.monotonic() + 180
        while not (a / "cancel-pid.txt").exists():
            terminal.pump()
            if time.monotonic() > deadline:
                raise AssertionError("Cancellation software fixture did not start")
        import win32api
        import win32event

        handle = win32api.OpenProcess(0x00100000, False, int((a / "cancel-pid.txt").read_text()))
        try:
            terminal.send("\x03")
            terminal.wait("cancelled", after=mark)
            assert win32event.WaitForSingleObject(handle, 5000) == 0
        finally:
            handle.Close()
        terminal.wait("agenthub>", after=mark)
        terminal.snapshot("19-cancelled-input")
        terminal.send("/exit\r")
        deadline = time.monotonic() + 15
        while terminal.proc.isalive() and time.monotonic() < deadline:
            terminal.pump()
        assert not terminal.proc.isalive() and terminal.proc.exitstatus == 0
        snapshot()
        assert evidence["runs"][-1]["result"]["state"] == "cancelled"
        evidence["terminal"] = {
            "kind": "Windows ConPTY",
            "exit_code": 0,
            "cancelled_process_exited": True,
            "restored_location": str(a),
        }
    finally:
        terminal.close()


if __name__ == "__main__":
    main()
