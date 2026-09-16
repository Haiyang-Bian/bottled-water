"""Explicit real-provider acceptance of an installed native CLI; never writes source home."""

import argparse
from dataclasses import asdict
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
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
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    _, profile = select_profile(load_config(args.source_home), args.profile)
    secret = LocalCredentialStore(args.source_home / "credentials").resolve(profile.credential_ref)
    redactor = Redactor([secret])
    python = str(args.python.resolve())
    uv = shutil.which("uv")
    assert uv, "uv must be explicitly installed for acceptance"
    base = Path(tempfile.mkdtemp(prefix="agenthub-native-live-"))
    home, reference, start, project = (base / name for name in ("home", "A 资料", "B", "C 项目"))
    for path in (reference, start, project):
        path.mkdir()
    spec = importlib.util.spec_from_file_location(
        "terminal_qa", Path(__file__).with_name("accept-cli-terminal.py")
    )
    terminal_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(terminal_module)
    value = {key: item for key, item in asdict(profile).items() if item is not None}
    value["credential_ref"] = "env:AGENTHUB_NATIVE_ACCEPT_KEY"
    save_config(home, {"default_profile": "qa", "profiles": {"qa": value}})
    env = {**os.environ, "AGENTHUB_HOME": str(home), "AGENTHUB_NATIVE_ACCEPT_KEY": secret,
           "UV_CACHE_DIR": str(base / "external cache"), "PYTHONUTF8": "1"}
    env.pop("PYTHONPATH", None)
    evidence = {"status": "running", "provider": profile.provider, "model": profile.model,
                "profile": args.profile, "python": python, "os": platform.platform(),
                "fixture": str(base), "runs": [], "checks": []}

    def cli(*arguments, cwd=start):
        return subprocess.run([python, "-B", "-m", "agent_cli.main", *arguments],
                              cwd=cwd, env=env, capture_output=True, encoding="utf-8", timeout=900)

    def runs():
        with sqlite3.connect((home / "state.sqlite3").as_uri() + "?mode=ro", uri=True) as db:
            return [json.loads(row[0]) if row[0] else None for row in db.execute(
                "SELECT result FROM runs ORDER BY created,id")]

    terminal = None
    try:
        evidence["version"] = cli("--version").stdout.strip()
        (reference / "spec.txt").write_text("add(a, b) 必须返回 a + b。验收标识 NativeABC。", "utf-8")
        (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", "utf-8")
        (project / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\nclass CalcTest(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n", "utf-8")
        (project / "slow.py").write_text(
            "import os, subprocess, sys, time\nfrom pathlib import Path\n"
            "child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(300)'])\n"
            "Path('tree-pids.txt').write_text(f'{os.getpid()} {child.pid}')\ntime.sleep(300)\n",
            "utf-8")
        subprocess.run([python, "-m", "venv", "--without-pip", str(project / ".venv")],
                       check=True, capture_output=True, timeout=60)
        project_python = str(project / ".venv/Scripts/python.exe")
        for command in (["git", "init", "-q"], ["git", "add", "calc.py", "test_calc.py"],
                        ["git", "-c", "user.name=Native QA", "-c", "user.email=qa@localhost",
                         "commit", "-qm", "fixture"]):
            subprocess.run(command, cwd=project, check=True, capture_output=True)
        install_args = ['pip', 'install', '--python', project_python, '--index-url',
                        'https://pypi.org/simple', 'colorama==0.4.6']
        prompt = (
            f"本次任务从 B 启动。用文件工具读取 {reference / 'spec.txt'}，根据要求修复 "
            f"{project / 'calc.py'}。先用 software.discover 在 {project} 发现项目解释器。"
            f"用 process.run 和该解释器运行 unittest（cwd={project}）。"
            f"然后用 process.run 执行程序 {uv}，参数数组为 "
            f"{json.dumps(install_args)}。"
            "这是明确授权的测试依赖联网安装，使用环境配置的 UV_CACHE_DIR。"
            f"安装后用项目解释器验证 import colorama 并打印版本，最后用 Git 查看 {project} "
            "的 diff。依据工具结果报告，勿提交 Git、登记软件或操作其他目录。"
        )
        first = cli("--json", "-p", prompt)
        (output / "first.jsonl").write_text(redactor.text(first.stdout), "utf-8")
        (output / "first.stderr.txt").write_text(redactor.text(first.stderr), "utf-8")
        records = [json.loads(line) for line in first.stdout.splitlines()]
        result = records[-1]
        evidence["runs"].append({**result, "exit_code": first.returncode})
        assert first.returncode == 0 and result["state"] == "completed", result
        session = result["context_scope_id"]
        with sqlite3.connect(home / "state.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM trusted").fetchone()[0] == 0
        check = subprocess.run([project_python, "-m", "unittest", "-v"], cwd=project,
                               capture_output=True, encoding="utf-8", timeout=60)
        assert check.returncode == 0, check.stderr
        installed = subprocess.run([project_python, "-c", "import colorama;print(colorama.__version__)"],
                                   cwd=project, capture_output=True, text=True, timeout=30)
        assert installed.returncode == 0 and installed.stdout.strip() == "0.4.6"
        assert any((base / "external cache").rglob("*")), "external cache was not written"
        names = [r["payload"].get("tool") for r in records if r.get("type") == "agent.tool_result"]
        assert "process.run" in names and "software.discover" in names
        evidence["checks"] += ["cross A/B/C read and edit", "project Python tests",
                               "explicit internet dependency install", "external uv cache", "no trust"]
        terminal = terminal_module.Terminal([python, "-B", "-m", "agent_cli.main", "-r"],
                                            reference, env, output)
        terminal.wait("选择任务")
        terminal.snapshot("01-global-selector")
        mark = terminal.send("\r")
        terminal.wait("agenthub>", after=mark)
        terminal.snapshot("02-history")
        mark = terminal.send(f'/cd "{project}"\r')
        terminal.wait("agenthub>", after=mark)
        followup = ("继续原任务，使用 process.run 和项目 .venv 的 Python 再跑 unittest，"
                    "然后用 Git 看 diff，报告原始错误和上一轮安装的依赖版本。不要再次安装。")
        mark = terminal.send(followup + "\r")
        deadline = time.monotonic() + 600
        while len(runs()) < 2 or runs()[-1] is None:
            terminal.pump()
            assert time.monotonic() < deadline, "resumed task deadline"
        resumed = runs()[-1]
        assert resumed["state"] == "completed" and resumed["context_scope_id"] == session
        evidence["runs"].append(resumed)
        terminal.wait("agenthub>", after=mark)
        terminal.snapshot("03-resumed-cwd")
        evidence["checks"] += ["no-ID global resume", "visible history", "cross-directory /cd"]
        mark = terminal.send(
            "请只用 process.run 调用项目 Python 运行 slow.py，timeout 120 秒。"
            "这是取消测试，我会按 Ctrl+C；不要修改该脚本或另开后台启动器。\r")
        deadline = time.monotonic() + 180
        while not (project / "tree-pids.txt").exists():
            terminal.pump()
            assert time.monotonic() < deadline, "long process was not started"
        import win32api
        import win32event
        handles = [win32api.OpenProcess(0x00100000, False, int(pid))
                   for pid in (project / "tree-pids.txt").read_text().split()]
        try:
            terminal.send("\x03")
            terminal.wait("cancelled", after=mark)
            assert all(win32event.WaitForSingleObject(h, 5000) == 0 for h in handles)
        finally:
            for handle in handles:
                handle.Close()
        terminal.wait("agenthub>", after=mark)
        terminal.snapshot("04-cancelled")
        evidence["runs"].append(runs()[-1])
        assert runs()[-1]["state"] == "cancelled"
        terminal.send("/exit\r")
        deadline = time.monotonic() + 15
        while terminal.proc.isalive() and time.monotonic() < deadline:
            terminal.pump()
        assert not terminal.proc.isalive() and terminal.proc.exitstatus == 0
        evidence["checks"].append("real-provider Ctrl+C cleans parent and child and restores input")
        plain = cli("--plain", "--resume", session, "-p",
                    "这是取消后的续接。只读取 calc.py，确认当前加法实现仍在，简短报告。")
        (output / "plain.txt").write_text(redactor.text(plain.stdout), "utf-8")
        assert plain.returncode == 0 and "\x1b" not in plain.stdout
        evidence["runs"].append({**runs()[-1], "exit_code": plain.returncode})
        evidence["checks"].append("plain restart after cancellation retains saved cwd")
        evidence["final_file_sha256"] = hashlib.sha256((project / "calc.py").read_bytes()).hexdigest()
        evidence["status"] = "passed"
    except BaseException as exc:
        evidence["status"], evidence["error"] = "failed", redactor.text(str(exc))
        raise
    finally:
        if terminal is not None:
            terminal.raw = redactor.text(terminal.raw)
            terminal.close()
        (output / "acceptance.json").write_text(redactor.dumps(evidence), "utf-8")


if __name__ == "__main__":
    main()
