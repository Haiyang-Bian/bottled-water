"""Installed CLI + real SDK/HTTP + real files/processes; model replies are deterministic fixtures."""

import json
import os
import subprocess
import sys
import threading
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
                        "finish_reason": "tool_calls" if "tool_calls" in delta else "stop",
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
