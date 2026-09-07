"""Installed CLI + real SDK/HTTP + real files/processes; model replies are deterministic fixtures."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def cli_fixture(tmp_path):
    project = tmp_path / "项目 A"
    project.mkdir()
    second = tmp_path / "项目 B"
    second.mkdir()
    (second / "note.txt").write_text("separate project", encoding="utf-8")
    (project / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(project)], check=True, capture_output=True)
    subprocess.run(["git", "add", "calc.py"], cwd=project, check=True, capture_output=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-qm",
            "baseline",
        ],
        cwd=project,
        check=True,
        capture_output=True,
    )
    requests = []
    steps = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            user = next(m["content"] for m in reversed(body["messages"]) if m["role"] == "user")
            scenario = user.split()[0]
            step = steps.get(scenario, 0)
            steps[scenario] = step + 1
            if scenario == "FAIL":
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(
                    b'{"error":{"message":"fixture unavailable","type":"server_error"}}'
                )
                return

            def call(prefix, arguments):
                name = next(
                    t["function"]["name"]
                    for t in body["tools"]
                    if t["function"]["description"].startswith(prefix)
                )
                return {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": f"call-{scenario}-{step}",
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(arguments)},
                        }
                    ]
                }

            delta = {
                "content": 'Finished.\n```status_report\n{"state":"completed","will":"complete"}\n```'
            }
            if scenario == "REPAIR":
                if step == 0:
                    delta = call("Read text", {"path": "calc.py"})
                elif step == 1:
                    observation = json.loads(body["messages"][-1]["content"])
                    delta = call(
                        "Replace exactly",
                        {
                            "path": "calc.py",
                            "old_text": "a - b",
                            "new_text": "a + b",
                            "expected_hash": observation["result"]["sha256"],
                        },
                    )
                elif step == 2:
                    python = sys.executable.replace("'", "''")
                    delta = call(
                        "Execute a non-interactive",
                        {
                            "script": f"& '{python}' -c \"from calc import add; assert add(2, 3) == 5; print('TEST PASSED')\""
                        },
                    )
                elif step == 3:
                    delta = call("Execute Git", {"args": ["diff", "--", "calc.py"]})
            elif scenario.startswith("CROSS"):
                if step == 0:
                    delta = call("Read text", {"path": str(second / "note.txt")})
                elif not json.loads(body["messages"][-1]["content"])["success"]:
                    delta = {
                        "content": 'Directory not authorized.\n```status_report\n{"state":"failed","will":"blocked"}\n```'
                    }
            elif scenario.startswith("SLOW") and step == 0:
                python = sys.executable.replace("'", "''")
                workload = str(Path(__file__).parent / "helpers" / "process_tree.py").replace(
                    "'", "''"
                )
                delta = call(
                    "Execute a non-interactive",
                    {
                        "script": f"& '{python}' '{workload}'",
                        "timeout": 90,
                    },
                )
            elif scenario == "SECRET":
                if step == 0:
                    delta = call(
                        "Execute a non-interactive",
                        {
                            "script": "Write-Output 'fixture-key-not-a-real-credential'",
                        },
                    )
                else:
                    delta = {
                        "content": "fixture-key-not-a-real-credential finished",
                        "reasoning_content": "private-reasoning-must-not-persist",
                    }
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()

            def send(choices, usage=None):
                event = {
                    "id": "fixture",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "fixture",
                    "choices": choices,
                }
                if usage:
                    event["usage"] = usage
                self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())

            send([{"index": 0, "delta": delta, "finish_reason": None}])
            send(
                [
                    {
                        "index": 0,
                        "delta": {},
                        "finish_reason": "length"
                        if scenario == "TRUNCATED"
                        else ("tool_calls" if "tool_calls" in delta else "stop"),
                    }
                ]
            )
            send([], {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42})
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = {
        **os.environ,
        "AGENTHUB_HOME": str(tmp_path / "home"),
        "AGENTHUB_TEST_KEY": "fixture-key-not-a-real-credential",
    }
    environment.pop("PYTHONPATH", None)
    python = os.environ.get("AGENTHUB_TEST_PYTHON", sys.executable)

    def run(*args, cwd=project, expected=0):
        result = subprocess.run(
            [python, "-B", "-m", "agent_cli.main", *args],
            cwd=cwd,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=45,
        )
        assert result.returncode == expected, (
            f"{result.returncode}: {result.stdout}\n{result.stderr}"
        )
        return result

    run.python = python
    run.environment = environment

    try:
        run(
            "init",
            "--provider",
            "openai_compatible",
            "--model",
            "fixture",
            "--base-url",
            f"http://127.0.0.1:{server.server_port}/v1",
            "--credential-env",
            "AGENTHUB_TEST_KEY",
        )
        yield run, project, second, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def records(result):
    return [json.loads(line) for line in result.stdout.splitlines() if line.strip()]


def test_cli_redacts_credentials_and_does_not_persist_private_reasoning(cli_fixture):
    import sqlite3

    run, project, _, _ = cli_fixture
    secret = run.environment["AGENTHUB_TEST_KEY"]
    run("trust", "add", str(project))
    response = run("--json", "-p", "SECRET")
    assert secret not in response.stdout + response.stderr
    assert "private-reasoning-must-not-persist" not in response.stdout + response.stderr
    assert "[redacted]" in response.stdout
    assert secret not in run("config", "show").stdout
    run_id = records(response)[-1]["run_id"]
    replay = run("replay", run_id).stdout
    state = Path(run.environment["AGENTHUB_HOME"])
    with sqlite3.connect(state / "state.sqlite3") as connection:
        persisted = "\n".join(connection.iterdump())
    diagnostics = "\n".join(p.read_text(encoding="utf-8") for p in (state / "logs").glob("*.log"))
    for text in (persisted, replay, diagnostics):
        assert secret not in text and "private-reasoning-must-not-persist" not in text


def test_cli_real_sdk_repair_resume_cross_directory_and_failure(cli_fixture):
    run, project, second, requests = cli_fixture
    rejected = run("--json", "-p", "REPAIR", expected=2)
    assert records(rejected)[0]["type"] == "error"
    assert not requests
    run("trust", "add", str(project))
    fixed = run("--json", "-p", "REPAIR")
    events = records(fixed)
    result = events[-1]
    assert result["type"] == "result" and result["state"] == "completed"
    assert result["usage"]["prompt_tokens"] == 150 and not result["usage"]["estimated"]
    assert "return a + b" in (project / "calc.py").read_text()
    outcomes = [e["payload"] for e in events if e["type"] == "agent.tool_result"]
    assert len(outcomes) == 4 and all(e["success"] for e in outcomes)
    assert "TEST PASSED" in outcomes[2]["result"]["stdout"]
    assert "+    return a + b" in outcomes[3]["result"]["stdout"]
    replay = records(run("replay", result["run_id"]))
    assert replay[-1]["type"] == "system.run_completed"
    run("--continue", "--json", "-p", "REMEMBER")
    assert any(m["role"] == "user" and m["content"] == "REPAIR" for m in requests[-1]["messages"])
    run("trust", "add", str(second))
    run("--json", "-p", "ISOLATED", cwd=second)
    assert not any("REPAIR" in m["content"] for m in requests[-1]["messages"])
    run("--continue", "--json", "-p", "CROSS_DENIED", expected=1)
    run("--continue", "--add-dir", str(second), "--json", "-p", "CROSS_ALLOWED")
    failed = records(run("--json", "-p", "FAIL", expected=1))[-1]
    assert failed["state"] == "failed"
    assert failed["usage"]["estimated"]
    truncated = records(run("--json", "-p", "TRUNCATED", expected=1))[-1]
    assert truncated["state"] == "failed" and truncated["reason_code"] == "output_token_limit_exceeded"
    assert truncated["usage"]["prompt_tokens"] == 30 and not truncated["usage"]["estimated"]
    assert len(json.loads(run("sessions").stdout)) >= 2
    if os.environ.get("AGENTHUB_TEST_PYTHON"):
        isolated = subprocess.run(
            [
                os.environ["AGENTHUB_TEST_PYTHON"],
                "-I",
                "-c",
                "import importlib.util; assert all(importlib.util.find_spec(n) is None for n in ['fastapi','sqlalchemy','redis','rapidocr_onnxruntime','docx'])",
            ],
            cwd=second,
            capture_output=True,
            timeout=20,
        )
        assert isolated.returncode == 0, isolated.stderr


@pytest.mark.skipif(os.name != "nt", reason="Windows Job Object / SIGINT lifecycle")
@pytest.mark.parametrize("mode", ["cancel", "crash"])
def test_cli_process_tree_lock_cancellation_and_crash_recovery(cli_fixture, mode):
    import win32api
    import win32event
    from agent_adapters.storage.sqlite import SQLiteStore

    run, project, _, _ = cli_fixture
    run("trust", "add", str(project))
    trigger = project / "interrupt.signal"
    wrapper = Path(__file__).parent / "helpers" / "cli_signal_host.py"
    handles = []
    with (project / "cli.out").open("wb") as stdout, (project / "cli.err").open("wb") as stderr:
        process = subprocess.Popen(
            [run.python, "-B", str(wrapper), str(trigger), "--json", "-p", "SLOW_" + mode],
            cwd=project,
            env=run.environment,
            stdout=stdout,
            stderr=stderr,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:
            deadline = time.monotonic() + 25
            pids_file = project / "tree-pids.txt"
            while not pids_file.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert pids_file.exists(), (project / "cli.err").read_text(encoding="utf-8")
            pids = pids_file.read_text(encoding="ascii").split()
            handles = [win32api.OpenProcess(0x00100000, False, int(pid)) for pid in pids]
            session = json.loads(run("sessions").stdout)[0]["id"]
            run("--resume", session, "--json", "-p", "BUSY", expected=3)
            # Another session can run while the first owns both its lock and live processes.
            run("--json", "-p", "OTHER_SESSION")
            store = SQLiteStore(Path(run.environment["AGENTHUB_HOME"]) / "state.sqlite3")
            try:
                row = store.db.execute(
                    "SELECT id,result FROM runs WHERE scope=?", (session,)
                ).fetchone()
                abandoned_run = row["id"]
                assert row["result"] is None
            finally:
                store.close()
            if mode == "cancel":
                trigger.write_text("SIGINT", encoding="ascii")
                assert process.wait(timeout=25) == 130
            else:
                process.kill()
                process.wait(timeout=10)
            for handle in handles:
                assert win32event.WaitForSingleObject(handle, 5000) == 0
            assert pids_file.exists()  # Side effects are retained, not rolled back.
            run("--resume", session, "--json", "-p", "AFTER_RESTART")
            events = records(run("replay", abandoned_run))
            terminal = [
                e
                for e in events
                if e["type"]
                in {"system.run_completed", "system.run_failed", "system.run_cancelled"}
            ]
            assert len(terminal) == 1
            assert terminal[0]["type"] == (
                "system.run_cancelled" if mode == "cancel" else "system.run_failed"
            )
            if mode == "crash":
                assert terminal[0]["payload"]["reason_code"] == "process_lost"
            else:
                output = [
                    json.loads(line)
                    for line in (project / "cli.out").read_text(encoding="utf-8").splitlines()
                ]
                assert output[-1]["state"] == "cancelled"
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=10)
            for handle in handles:
                handle.Close()
