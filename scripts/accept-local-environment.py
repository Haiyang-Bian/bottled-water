"""Explicit real-provider L1 acceptance against an isolated installed CLI and ConPTY."""

import argparse
import contextlib
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import time

from agent_adapters.credentials.local import LocalCredentialStore
from agent_cli.config import load_config, save_config, select_profile
from agent_subsystems.observability.redaction import Redactor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-home", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    source_config = load_config(args.source_home)
    _, profile = select_profile(source_config, args.profile)
    secret = LocalCredentialStore(args.source_home / "credentials").resolve(profile.credential_ref)
    redactor = Redactor([secret])
    spec = importlib.util.spec_from_file_location("terminal_qa", Path(__file__).with_name(
        "accept-cli-terminal.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    python = str(args.python.resolve())
    evidence = {"status": "running", "provider": profile.provider, "model": profile.model,
                "profile": args.profile, "python": python, "checks": [], "runs": [],
                "configured_providers": sorted({p["provider"] for p in
                                                 source_config["profiles"].values()})}
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="agenthub-l1-live-") as temporary:
            base = Path(temporary)
            home, a, b, c, d = [base / name for name in ("home", "甲 A", "乙 B", "丙 C", "丁 D")]
            for directory in (a, b, c):
                directory.mkdir()
            (a / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (a / "test_calc.py").write_text(
                "import unittest\nfrom calc import add\nclass TestCalc(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
            for argv in (["git", "init", "-q"], ["git", "add", "."],
                         ["git", "-c", "user.name=L1 QA", "-c", "user.email=qa@localhost",
                          "commit", "-qm", "fixture"]):
                subprocess.run(argv, cwd=a, check=True, capture_output=True)
            value = {k: v for k, v in asdict(profile).items() if v is not None}
            value["credential_ref"] = "env:AGENTHUB_L1_LIVE_KEY"
            save_config(home, {"default_profile": "qa", "profiles": {"qa": value}})
            env = {**os.environ, "AGENTHUB_HOME": str(home), "AGENTHUB_L1_LIVE_KEY": secret}
            env.pop("PYTHONPATH", None)

            def cli(cwd, *arguments):
                return subprocess.run([python, "-B", "-m", "agent_cli.main", *arguments],
                                      cwd=cwd, env=env, capture_output=True, encoding="utf-8",
                                      timeout=360)

            def rows():
                with contextlib.closing(sqlite3.connect(
                    (home / "state.sqlite3").as_uri() + "?mode=ro", uri=True
                )) as db:
                    return [{"run_id": rid, "request": json.loads(request),
                             "result": json.loads(result) if result else None}
                            for rid, request, result in db.execute(
                                "SELECT id,request,result FROM runs ORDER BY created,id")]

            def recorded(name, cwd, *arguments, expected=0):
                result = cli(cwd, *arguments)
                (output / (name + ".txt")).write_text(redactor.text(result.stdout), encoding="utf-8")
                (output / (name + ".stderr.txt")).write_text(
                    redactor.text(result.stderr), encoding="utf-8")
                evidence["checks"].append({"name": name, "exit_code": result.returncode})
                evidence["runs"] = rows()
                assert result.returncode == expected, redactor.text(result.stderr + result.stdout)
                return result

            def latest(cwd, scope=None):
                evidence["runs"] = rows()
                record = evidence["runs"][-1]
                result = record["result"]
                assert result and result["state"] == "completed", record
                metadata = record["request"]["metadata"]
                assert metadata["execution_location"]["cwd"] == os.path.normcase(str(cwd.resolve()))
                if scope:
                    assert result["context_scope_id"] == scope
                return result["context_scope_id"]

            for directory in (a, b, c):
                assert cli(b, "trust", "add", str(directory)).returncode == 0
            evidence["version"] = cli(b, "--version").stdout.strip()
            recorded("01-repair-json", a, "--json", "-p",
                     "用文件工具读取并修复 calc.py 的 add 函数，让测试通过。"
                     f"用 PowerShell 执行 & '{python}' -m unittest -v，"
                     "再用 Git 查看 diff，依据实际结果报告。无需安装依赖，不要提交。")
            scope = latest(a)
            assert subprocess.run([python, "-m", "unittest", "-v"], cwd=a,
                                  capture_output=True).returncode == 0
            terminal = module.Terminal([python, "-B", "-m", "agent_cli.main", "-r"], b, env, output)
            try:
                terminal.wait("选择任务")
                terminal.snapshot("02-selector-from-b")
                mark = terminal.send("\r")
                terminal.wait("agenthub>", after=mark)
                terminal.snapshot("03-restored-a")

                def task(prompt, expected_cwd):
                    count = len(rows())
                    mark = terminal.send(prompt + "\r")
                    deadline = time.monotonic() + 360
                    while True:
                        terminal.pump()
                        records = rows()
                        if len(records) > count and records[-1]["result"]:
                            break
                        if time.monotonic() > deadline:
                            raise AssertionError("Interactive real task exceeded deadline")
                    terminal.wait("agenthub>", after=mark + 10)
                    latest(expected_cwd, scope)

                task("继续上次任务，使用 Git 查看 diff，并用 PowerShell 输出当前位置。"
                     "根据历史及实际结果说明原错误和修复的函数，不修改文件。", a)
                terminal.snapshot("04-resumed-result")
                mark = terminal.send(f'/add-dir "{c}"\r')
                terminal.wait("已更新", after=mark)
                mark = terminal.send(f'/cd "{c}"\r')
                terminal.wait("agenthub>", after=mark)
                terminal.snapshot("05-changed-location")
                task("现在用文件工具在当前工作位置创建 result.txt，内容是 L1_C_OK。"
                     "然后用 PowerShell 输出当前位置并读取 result.txt，依据实际结果报告。", c)
                assert (c / "result.txt").read_text(encoding="utf-8").strip() == "L1_C_OK"
                assert not (a / "result.txt").exists() and not (b / "result.txt").exists()
                terminal.snapshot("06-c-result")
                terminal.send("/exit\r")
                deadline = time.monotonic() + 15
                while terminal.proc.isalive() and time.monotonic() < deadline:
                    terminal.pump()
                assert not terminal.proc.isalive() and terminal.proc.exitstatus == 0
                evidence["interactive_exit_code"] = terminal.proc.exitstatus
            finally:
                terminal.raw = redactor.text(terminal.raw)
                terminal.close()
            recorded("07-continue-c-plain", b, "--plain", "-c", "-p",
                     "用文件工具读取当前工作位置的 result.txt，原样报告其内容。")
            latest(c, scope)
            recorded("08-new-b-json", b, "--json", "-p",
                     "这是一个新任务，只回复 B_NEW_TASK，不调用工具。")
            assert latest(b) != scope
            c.rename(d)
            recorded("09-missing-location", b, "--json", "--resume", scope,
                     "-p", "This must not execute", expected=2)
            recorded("10-read-history", b, "--json", "history", scope)
            assert cli(b, "trust", "add", str(d)).returncode == 0
            recorded("11-repair-location", b, "--json", "--resume", scope,
                     "--add-dir", str(d), "--cwd", str(d), "-p",
                     f"我已用 --cwd 将工作位置从 C 改到 {d}。"
                     "请使用文件工具读取相对路径 result.txt，原样报告内容。"
                     "先前消息中的 C 绝对路径已失效，不要继续沿用。")
            latest(d, scope)
            evidence["runs"] = rows()
            with contextlib.closing(sqlite3.connect(
                (home / "state.sqlite3").as_uri() + "?mode=ro", uri=True
            )) as db:
                evidence["control_events"] = [json.loads(r[0]) for r in db.execute(
                    "SELECT body FROM session_events ORDER BY version")]
                evidence["tool_results"] = [json.loads(r[0]) for r in db.execute(
                    "SELECT body FROM events ORDER BY run,sequence")
                    if json.loads(r[0]).get("type") == "agent.tool_result"]
            evidence["final_file_sha256"] = hashlib.sha256((d / "result.txt").read_bytes()).hexdigest()
            evidence["status"] = "passed"
    except BaseException as exc:
        evidence["status"], evidence["error"] = "failed", redactor.text(str(exc))
        raise
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "acceptance.json").write_text(redactor.dumps(evidence), encoding="utf-8")


if __name__ == "__main__":
    main()
