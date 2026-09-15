"""Explicit-profile, installed CLI task plus no-ID recovery through real ConPTY."""

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
    parser.add_argument("--plain-only", action="store_true")
    args = parser.parse_args()
    _, profile = select_profile(load_config(args.source_home), args.profile)
    secret = LocalCredentialStore(args.source_home / "credentials").resolve(profile.credential_ref)
    redactor = Redactor([secret])
    spec = importlib.util.spec_from_file_location("terminal_qa", Path(__file__).with_name(
        "accept-cli-terminal.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    python = str(args.python.resolve())
    evidence = {"provider": profile.provider, "model": profile.model, "profile": args.profile,
                "python": python, "status": "running", "runs": []}
    try:
        with tempfile.TemporaryDirectory(prefix="agenthub-live-ui-") as temporary:
            base = Path(temporary)
            home, project = base / "home", base / "项目"
            project.mkdir()
            (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
            (project / "test_calc.py").write_text(
                "import unittest\nfrom calc import add\nclass TestCalc(unittest.TestCase):\n"
                "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", encoding="utf-8")
            for argv in (["git", "init", "-q"], ["git", "add", "."],
                         ["git", "-c", "user.name=CLI QA", "-c", "user.email=qa@localhost",
                          "commit", "-qm", "fixture"]):
                subprocess.run(argv, cwd=project, check=True, capture_output=True)
            value = {key: item for key, item in asdict(profile).items() if item is not None}
            value["credential_ref"] = "env:AGENTHUB_LIVE_UI_KEY"
            save_config(home, {"default_profile": "qa", "profiles": {"qa": value}})
            env = {**os.environ, "AGENTHUB_HOME": str(home), "AGENTHUB_LIVE_UI_KEY": secret}
            env.pop("PYTHONPATH", None)
            def cli(*arguments):
                return subprocess.run([python, "-B", "-m", "agent_cli.main", *arguments],
                                      cwd=project, env=env, capture_output=True, encoding="utf-8",
                                      timeout=300)
            assert cli("trust", "add", str(project)).returncode == 0
            prompt = ("请用文件工具读取并修复 calc.py 的 add 函数，让测试通过。"
                      f"用 PowerShell 执行 & '{python}' -m unittest -v，"
                      "再用 Git 查看 diff，依据实际工具结果报告。无需安装依赖，也不要提交仓库。")
            evidence["version"] = cli("--version").stdout.strip()
            first = cli("--plain" if args.plain_only else "--json", "-p", prompt)
            (output / ("first.txt" if args.plain_only else "first.jsonl")).write_text(
                redactor.text(first.stdout), encoding="utf-8")
            (output / "first.stderr.txt").write_text(redactor.text(first.stderr), encoding="utf-8")
            if args.plain_only:
                with contextlib.closing(sqlite3.connect(
                    (home / "state.sqlite3").as_uri() + "?mode=ro", uri=True
                )) as db:
                    records = [json.loads(db.execute(
                        "SELECT result FROM runs ORDER BY created DESC,id DESC LIMIT 1"
                    ).fetchone()[0])]
                assert "\x1b" not in first.stdout + first.stderr
            else:
                records = [json.loads(line) for line in first.stdout.splitlines()]
            evidence["runs"].append({**records[-1], "exit_code": first.returncode})
            assert first.returncode == 0, redactor.text(first.stderr)
            check = subprocess.run([python, "-m", "unittest", "-v"], cwd=project,
                                   capture_output=True, encoding="utf-8")
            evidence["test_exit_code"] = check.returncode
            assert check.returncode == 0
            if args.plain_only:
                evidence["status"] = "passed"
                evidence["mode"] = "plain"
                return
            terminal = module.Terminal([python, "-B", "-m", "agent_cli.main", "-r"],
                                       project, env, output)
            try:
                terminal.wait("选择任务")
                terminal.snapshot("01-live-selector")
                mark = terminal.send("\r")
                terminal.wait("agenthub>", after=mark)
                terminal.snapshot("02-live-restored")
                followup = ("继续上一轮。请使用 Git 工具检查当前 diff，再运行一次上一轮的 unittest，"
                            "说明我们修复了哪个函数以及原来的错误。无需继续修改文件。")
                mark = terminal.send(followup + "\r")
                deadline = time.monotonic() + 300
                while True:
                    terminal.pump()
                    with contextlib.closing(sqlite3.connect(
                        (home / "state.sqlite3").as_uri() + "?mode=ro", uri=True
                    )) as db:
                        rows = db.execute("SELECT result FROM runs ORDER BY created,id").fetchall()
                    if len(rows) >= 2 and rows[-1][0]:
                        resumed = json.loads(rows[-1][0])
                        evidence["runs"].append(resumed)
                        break
                    if time.monotonic() > deadline:
                        raise AssertionError("resumed real task exceeded acceptance deadline")
                terminal.wait("agenthub>", after=mark + 10)
                terminal.snapshot("03-live-completed")
                assert resumed["state"] == "completed", resumed["reason_code"]
                assert resumed["context_scope_id"] == records[-1]["context_scope_id"]
                assert resumed["counters"]["tool_calls"] >= 1
                terminal.send("/exit\r")
                deadline = time.monotonic() + 15
                while terminal.proc.isalive() and time.monotonic() < deadline:
                    terminal.pump()
                evidence["interactive_exit_code"] = terminal.proc.exitstatus
                assert not terminal.proc.isalive() and terminal.proc.exitstatus == 0
            finally:
                terminal.raw = redactor.text(terminal.raw)
                terminal.close()
            evidence["final_file_sha256"] = hashlib.sha256((project / "calc.py").read_bytes()).hexdigest()
        evidence["status"] = "passed"
    except BaseException as exc:
        evidence["status"], evidence["error"] = "failed", redactor.text(str(exc))
        raise
    finally:
        (output / "acceptance.json").write_text(redactor.dumps(evidence), encoding="utf-8")


if __name__ == "__main__":
    main()
