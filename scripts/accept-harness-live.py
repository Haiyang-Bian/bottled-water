"""Explicit-profile installed CLI acceptance in disposable Git projects.

This makes paid requests only when --profile and --source-home are both provided.
JSON evidence contains redacted events, artifact checks, timings and incomplete items.
"""

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from agent_adapters.credentials.local import LocalCredentialStore
from agent_cli.config import load_config, save_config, select_profile
from agent_subsystems.observability.redaction import Redactor


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-home", type=Path, required=True)
    parser.add_argument("--profile")
    parser.add_argument("--recovery-only", action="store_true")
    parser.add_argument("--list-profiles", action="store_true")
    parser.add_argument("--python", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    config = load_config(args.source_home)
    if args.list_profiles:
        print(
            json.dumps(
                [
                    {"name": name, "provider": value.get("provider"), "model": value.get("model")}
                    for name, value in config.get("profiles", {}).items()
                ],
                ensure_ascii=False,
            )
        )
        return 0
    if not args.profile or not args.python or not args.output:
        parser.error("Explicit profile, installed Python and output are required")
    name, profile = select_profile(config, args.profile)
    secret = LocalCredentialStore(args.source_home / "credentials").resolve(profile.credential_ref)
    redactor = Redactor([secret])
    python = str(args.python.resolve())
    records = []
    evidence = {
        "provider": profile.provider,
        "model": profile.model,
        "profile": name,
        "python": python,
        "scope": "recovery_only" if args.recovery_only else "full",
        "records": records,
        "status": "running",
    }

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(redactor.dumps(evidence), encoding="utf-8")

    with tempfile.TemporaryDirectory(prefix="agenthub-live-") as temporary:
        base = Path(temporary)
        home, project, second = base / "home", base / "project", base / "second"
        project.mkdir()
        second.mkdir()
        (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
        (project / "test_calc.py").write_text(
            "import unittest\nfrom calc import add\nclass TestCalc(unittest.TestCase):\n"
            "    def test_add(self):\n        self.assertEqual(add(2, 3), 5)\n",
            encoding="utf-8",
        )
        (second / "factor.txt").write_text("7\n", encoding="utf-8")
        for argv in (
            ["git", "init"],
            ["git", "add", "calc.py", "test_calc.py"],
            [
                "git",
                "-c",
                "user.name=Harness Test",
                "-c",
                "user.email=harness@localhost",
                "commit",
                "-m",
                "fixture",
            ],
        ):
            subprocess.run(argv, cwd=project, check=True, capture_output=True)
        configured = {k: v for k, v in asdict(profile).items() if v is not None}
        configured["credential_ref"] = "env:AGENTHUB_LIVE_KEY"
        local_config = {"default_profile": name, "profiles": {name: configured}}
        save_config(home, local_config)
        env = {**os.environ, "AGENTHUB_HOME": str(home), "AGENTHUB_LIVE_KEY": secret}
        env.pop("PYTHONPATH", None)

        def command(*argv, cwd=project, expected=0):
            result = subprocess.run(
                [python, "-B", "-m", "agent_cli.main", *argv],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=300,
            )
            assert secret not in result.stdout + result.stderr
            assert result.returncode == expected, (
                f"CLI exit {result.returncode}, expected {expected}"
            )
            return result

        def record(task, result, elapsed):
            events = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
            final = next((e for e in reversed(events) if e.get("type") == "result"), None)
            records.append(
                {
                    "task": task,
                    "exit_code": result.returncode,
                    "elapsed_seconds": elapsed,
                    "result": final,
                    "events": events,
                }
            )
            save()
            return final

        def task(label, prompt, *flags, cwd=project, expected=0):
            started = time.monotonic()
            result = subprocess.run(
                [python, "-B", "-m", "agent_cli.main", "--json", *flags, "-p", prompt],
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=300,
            )
            assert secret not in result.stdout + result.stderr
            final = record(label, result, time.monotonic() - started)
            assert result.returncode == expected and final, f"{label}: unexpected terminal"
            return final

        try:
            evidence["installed_version"] = command("--version").stdout.strip()
            evidence["doctor"] = json.loads(command("doctor").stdout)
            command("trust", "add", str(project))
            command("trust", "add", str(second))
            if not args.recovery_only:
                task(
                    "inspect",
                    "Remember SESSION_ORIGIN_R43 in this conversation only. Read calc.py "
                    "and explain its current behavior. Do not edit files or persist the marker in files.",
                )
                task(
                    "repair_and_test",
                    "Fix the add function in calc.py to add correctly. "
                    f"Run the actual tests using powershell.run with Python '{python}' -m unittest. "
                    "Only modify calc.py and report actual test results.",
                    "--continue",
                )
                check = subprocess.run(
                    [python, "-B", "-m", "unittest"], cwd=project, capture_output=True, timeout=30
                )
                assert check.returncode == 0
                task(
                    "explain_diff",
                    "Read the actual git diff using git.run and explain the change briefly.",
                    "--continue",
                )
                task(
                    "restart_followup",
                    "Continue our previous fix: add a concise docstring to the function "
                    "we changed, preserve its behavior, and run tests again.",
                    "--continue",
                )
                isolated = task(
                    "directory_isolation",
                    "What SESSION_ORIGIN marker did I mention earlier? "
                    "If this conversation has none, reply UNKNOWN. Do not inspect files.",
                    cwd=second,
                )
                assert "SESSION_ORIGIN_R43" not in isolated["output"]
                task(
                    "cross_directory",
                    f"Read '{second / 'factor.txt'}' and calc.py using file tools. "
                    "Report add(2,3) multiplied by the factor, with the actual read evidence; do not edit.",
                    "--continue",
                    "--add-dir",
                    str(second),
                )
                diff = subprocess.check_output(["git", "diff"], cwd=project).decode("utf-8")
                evidence["diff"] = diff
                evidence["calc_sha256"] = hashlib.sha256(
                    (project / "calc.py").read_bytes()
                ).hexdigest()

            if os.name == "nt":
                import win32api
                import win32event

                for mode in ("cancel", "crash"):
                    pidfile, trigger = project / f"{mode}.pid", project / f"{mode}.signal"
                    out, err = project / f"{mode}.jsonl", project / f"{mode}.err"
                    script = f"$PID | Set-Content -LiteralPath '{pidfile}' -Encoding ascii; Start-Sleep -Seconds 90"
                    prompt = (
                        "Run this exact PowerShell script once using powershell.run; do not run other tools: "
                        + script
                    )
                    started = time.monotonic()
                    with out.open("wb") as stdout, err.open("wb") as stderr:
                        process = subprocess.Popen(
                            [
                                python,
                                "-B",
                                str(ROOT / "tests/helpers/cli_signal_host.py"),
                                str(trigger),
                                "--json",
                                "-p",
                                prompt,
                            ],
                            cwd=project,
                            env=env,
                            stdout=stdout,
                            stderr=stderr,
                            creationflags=subprocess.CREATE_NO_WINDOW,
                        )
                        handle = None
                        try:
                            deadline = time.monotonic() + 180
                            while (
                                not pidfile.exists()
                                and process.poll() is None
                                and time.monotonic() < deadline
                            ):
                                time.sleep(0.05)
                            assert pidfile.exists(), f"{mode}: long process did not start"
                            # Wait for Set-Content to finish before reading its PID.
                            for _ in range(40):
                                pid = pidfile.read_text(encoding="ascii").strip()
                                if pid:
                                    break
                                time.sleep(0.05)
                            handle = win32api.OpenProcess(0x00100000, False, int(pid))
                            if mode == "cancel":
                                trigger.write_text("SIGINT", encoding="ascii")
                            else:
                                process.kill()
                            code = process.wait(timeout=25)
                            assert mode == "crash" or code == 130
                            assert win32event.WaitForSingleObject(handle, 5000) == 0
                        finally:
                            if process.poll() is None:
                                process.kill()
                                process.wait(timeout=10)
                            if handle is not None:
                                handle.Close()
                    observed = subprocess.CompletedProcess(
                        [],
                        process.returncode,
                        out.read_text(encoding="utf-8"),
                        err.read_text(encoding="utf-8"),
                    )
                    record(mode, observed, time.monotonic() - started)
                    interrupted_record = records[-1]
                    interrupted_id = interrupted_record["events"][0]["run_id"]
                    interrupted_record["run_id"] = interrupted_id
                    interrupted_record["process_cleanup_verified"] = True
                    pid_mtime = pidfile.stat().st_mtime_ns
                    task(
                        mode + "_continuation",
                        "The previous long command was interrupted externally. "
                        "Do not replay it. Briefly report the recorded stop reason and whether any "
                        "operation result is unknown, based on continuation observations. "
                        "Then complete this new concrete task: read calc.py, ensure add returns "
                        "the sum, add a concise docstring if missing, and execute the tests with "
                        f"powershell.run using '{python}' -m unittest. "
                        "Report the outcome of THIS task. An unknown old sleep result is expected "
                        "and does not prevent this new repair/test task from completing.",
                        "--continue",
                    )
                    replay = [
                        json.loads(line)
                        for line in command("replay", interrupted_id).stdout.splitlines()
                    ]
                    terminal = next(
                        e
                        for e in reversed(replay)
                        if e["type"] in {"system.run_failed", "system.run_cancelled"}
                    )
                    assert terminal["payload"]["reason_code"] == (
                        "process_lost" if mode == "crash" else "user_cancelled"
                    )
                    interrupted_record["recovered_terminal"] = terminal
                    assert pidfile.stat().st_mtime_ns == pid_mtime, (
                        "Continuation rewrote the PID side effect"
                    )
                    for event in records[-1]["events"]:
                        if event["type"] != "agent.tool_call":
                            continue
                        for call in event["payload"].get("calls", []):
                            function = call.get("function", {})
                            if function.get("name") == "powershell.run":
                                script_text = (
                                    json.loads(function["arguments"]).get("script", "").lower()
                                )
                                assert "start-sleep" not in script_text, (
                                    "Continuation replayed the interrupted sleep"
                                )
                    interrupted_record["no_replay_verified"] = True
                    save()
            # Controlled transport outage uses the actual configured adapter, with an unreachable local endpoint.
            configured["base_url"] = "http://127.0.0.1:1"
            configured["timeout_seconds"] = 3
            save_config(home, local_config)
            outage = task("injected_transport_outage", "Reply OK", "--continue", expected=1)
            assert outage["state"] == "failed"
            assert "system.run_failed" in command("replay", outage["run_id"]).stdout
            evidence["status"] = "passed"
            evidence["limitations"] = [
                "Transport outage is locally injected, not a Provider service outage."
            ]
        except Exception as exc:
            evidence["status"] = "failed"
            evidence["failure"] = redactor.text(f"{type(exc).__name__}: {exc}")
        finally:
            save()
    print(
        json.dumps(
            {
                "status": evidence["status"],
                "provider": profile.provider,
                "output": str(args.output),
                "tasks": len(records),
            },
            ensure_ascii=False,
        )
    )
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
