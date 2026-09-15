"""Explicit real-provider L2 validation; never opens the daily state database."""

import argparse
import contextlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
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
    redactor, python, output = Redactor([secret]), str(args.python.resolve()), args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    evidence = {
        "status": "running",
        "provider": profile.provider,
        "model": profile.model,
        "profile": args.profile,
        "python": python,
        "checks": [],
        "runs": [],
        "configured_providers": sorted({p["provider"] for p in config["profiles"].values()}),
    }
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="agenthub-l2-live-") as temporary:
            base = Path(temporary)
            home, a, b = base / "home", base / "甲 A", base / "乙 B"
            a.mkdir()
            b.mkdir()
            (a / "source.txt").write_text(
                "项目甲的测试命令是 python -m unittest。\n", encoding="utf-8"
            )
            value = {k: v for k, v in asdict(profile).items() if v is not None}
            value["credential_ref"] = "env:AGENTHUB_L2_LIVE_KEY"
            save_config(home, {"default_profile": "qa", "profiles": {"qa": value}})
            env = {**os.environ, "AGENTHUB_HOME": str(home), "AGENTHUB_L2_LIVE_KEY": secret}
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
                    for name, kind in (
                        ("memory_used", "agent.memory_used"),
                        ("tool_results", "agent.tool_result"),
                    ):
                        evidence[name] = [
                            json.loads(r[0])
                            for r in db.execute(
                                "SELECT body FROM events WHERE body LIKE ? ORDER BY run,sequence",
                                ("%" + kind + "%",),
                            )
                        ]

            def cli(name, cwd, *arguments):
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
                assert result.returncode == 0, redactor.text(result.stdout + result.stderr)
                return result.stdout

            def model(name, cwd, prompt, *, plain=False):
                cli(name, cwd, "--plain" if plain else "--json", "--profile", "qa", "-p", prompt)
                row = evidence["runs"][-1]
                assert row["result"]["state"] == "completed", row
                return [e for e in evidence["memory_used"] if e["run_id"] == row["run_id"]]

            evidence["version"] = cli("version", b, "--version").strip()
            cli("trust-a", a, "trust", "add", str(a))
            cli("trust-b", b, "trust", "add", str(b))
            saved = json.loads(
                cli(
                    "01-save",
                    a,
                    "--json",
                    "memory",
                    "add",
                    "--title",
                    "语言偏好",
                    "--body",
                    "默认用中文解释，答案首句以『说明』开头。",
                    "--basic",
                )
            )
            used = model("02-use-in-b", b, "请解释测试为何有用，遵循已批准的语言偏好。")
            assert saved["id"] in json.dumps(used)
            assert "说明" in evidence["runs"][-1]["result"]["output"]
            model(
                "03-observe-propose",
                a,
                "读取 source.txt，然后用 memory.propose 提案一次："
                "title=项目甲测试指南L2，body 精确为『项目甲的测试命令是 python -m unittest。』，"
                f"kind=experience，evidence=observed，directory={a}。sources 使用 file.read 的 call_id，"
                "不必提供 sequence。不声称已经长期保存。",
            )
            candidates = json.loads(cli("04-candidates", b, "--json", "memory", "candidates"))
            candidate = next(c for c in candidates if c["content"]["title"] == "项目甲测试指南L2")
            assert candidate["status"] == "ready" and candidate["sources"][0]["sha256"], candidate
            assert not json.loads(
                cli("05-unapproved", b, "--json", "memory", "search", "项目甲测试指南L2")
            )
            adopted = json.loads(
                cli("06-adopt", b, "--json", "memory", "adopt", candidate["id"], "--revision", "1")
            )
            identifier = adopted["memory_id"]
            cli("07-revoke-a", b, "trust", "remove", str(a))
            used = model(
                "08-recall",
                b,
                "用 memory.search 搜索『项目甲测试指南L2』，再用 memory.read 读取，说明命令及来源。不要读取来源文件。",
                plain=True,
            )
            assert identifier in json.dumps(used)
            cli(
                "09-edit",
                b,
                "--json",
                "memory",
                "edit",
                identifier,
                "--revision",
                "1",
                "--body",
                "项目甲的测试命令现在是 python -m unittest -v。",
            )
            used = model("10-revised", b, "读取『项目甲测试指南L2』，说明最新修订的命令。")
            assert any(
                i["id"] == identifier and i["revision"] == 2
                for e in used
                for i in e["payload"]["items"]
            )
            cli(
                "11-forget", b, "--json", "memory", "forget", identifier, "--revision", "2", "--yes"
            )
            cli("12-rebuild", b, "--json", "memory", "rebuild")
            used = model(
                "13-forgotten",
                b,
                "用 memory.search 搜索『项目甲测试指南L2』。若无获准记忆，明确说没有，不猜测正文。",
            )
            assert identifier not in json.dumps(used)
            terminal_check(python, b, env, output, evidence, snapshot)
            evidence["status"] = "passed"
    except BaseException as exc:
        evidence["status"], evidence["error"] = "failed", redactor.text(str(exc))
        raise
    finally:
        evidence["elapsed_seconds"] = round(time.monotonic() - started, 2)
        (output / "acceptance.json").write_text(redactor.dumps(evidence), encoding="utf-8")


def terminal_check(python, project, env, output, evidence, snapshot):
    spec = importlib.util.spec_from_file_location(
        "terminal_qa", Path(__file__).with_name("accept-cli-terminal.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    terminal = module.Terminal(
        [python, "-B", "-m", "agent_cli.main", "--profile", "qa"], project, env, output
    )
    try:
        terminal.wait("agenthub>")
        mark = terminal.send("/memory\r")
        terminal.wait("选择记忆", after=mark)
        terminal.snapshot("14-memory-selector")
        terminal.send("\x1b")
        terminal.wait("agenthub>", after=mark)
        mark = terminal.send(
            "记住：先展示测试结果。先用 memory.propose 提出 user_stated 偏好候选，正文精确为『先展示测试结果』，"
            "来源是当前 request。然后 powershell.run 执行 $PID | Set-Content -Encoding ascii memory-cancel-pid.txt; Start-Sleep -Seconds 120\r"
        )
        deadline = time.monotonic() + 180
        while not (project / "memory-cancel-pid.txt").exists():
            terminal.pump()
            if time.monotonic() > deadline:
                raise AssertionError("Cancellation fixture did not start")
        import win32api
        import win32event

        handle = win32api.OpenProcess(
            0x00100000, False, int((project / "memory-cancel-pid.txt").read_text().strip())
        )
        try:
            terminal.send("\x03")
            terminal.wait("cancelled", after=mark)
            assert win32event.WaitForSingleObject(handle, 5000) == 0
        finally:
            win32api.CloseHandle(handle)
        terminal.wait("agenthub>", after=mark)
        terminal.snapshot("15-cancelled-input")
        mark = terminal.send("/memory candidates\r")
        terminal.wait("记忆候选", after=mark)
        terminal.snapshot("16-candidate-review")
        terminal.send("\x1b")
        terminal.wait("agenthub>", after=mark)
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
        }
    finally:
        terminal.close()


if __name__ == "__main__":
    main()
