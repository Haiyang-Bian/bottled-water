"""Real CLI processes and captured HTTP requests across independent task directories."""

import json
import os
import subprocess
from pathlib import Path

from test_cli_integration import cli_fixture as cli_fixture


def test_memory_cli_cross_task_lifecycle_and_candidate(cli_fixture):
    run, a, b, requests = cli_fixture

    def memory(*args, expected=0):
        return run("--json", "memory", *args, cwd=b, expected=expected)

    saved = json.loads(memory("add", "--title", "语言偏好", "--body", "默认用中文解释").stdout)
    run("trust", "add", str(b))
    run("--json", "-p", "B_CHECK", cwd=b)
    assert any("默认用中文解释" in m["content"] for m in requests[-1]["messages"])
    assert not any(str(a) in m["content"] for m in requests[-1]["messages"])
    assert saved["id"] in memory("used").stdout
    memory("edit", saved["id"], "--body", "默认使用英文", expected=2)
    memory("edit", saved["id"], "--revision", "1", "--body", "默认使用英文")
    memory("edit", saved["id"], "--revision", "1", "--body", "stale", expected=2)
    run("--json", "-p", "B_NEXT", cwd=b)
    assert "默认用中文解释" not in json.dumps(requests[-1]["messages"], ensure_ascii=False)
    memory("forget", saved["id"], "--revision", "2", expected=2)
    memory("forget", saved["id"], "--revision", "2", "--yes")
    memory("rebuild")
    assert json.loads(memory("list").stdout) == []
    run("--json", "-p", "MEMORYPROPOSE 记住：默认用中文解释", cwd=b)
    candidates = json.loads(memory("candidates").stdout)
    assert candidates[0]["status"] == "ready", candidates
    assert json.loads(memory("list").stdout) == []
    run("--json", "-p", "B_BEFORE_ADOPTION", cwd=b)
    assert "默认用中文解释" not in json.dumps(requests[-1]["messages"], ensure_ascii=False)
    memory("adopt", candidates[0]["id"], "--revision", "1", "--basic")
    run("--json", "-p", "B_AFTER_ADOPTION", cwd=b)
    assert "默认用中文解释" in json.dumps(requests[-1]["messages"], ensure_ascii=False)


def test_memory_management_without_credentials_or_task_creation(cli_fixture):
    run, a, b, requests = cli_fixture
    isolated = a / "memory-only-home"
    env = {**run.environment, "AGENTHUB_HOME": str(isolated)}
    env.pop("AGENTHUB_TEST_KEY", None)

    def command(*args):
        return subprocess.run(
            [run.python, "-B", "-m", "agent_cli.main", "--json", "memory", *args],
            cwd=b,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )

    result = command("list")
    assert result.returncode == 0 and not isolated.exists()
    result = command("add", "--title", "测试", "--body", "无模型保存")
    assert result.returncode == 0, result.stderr
    assert not requests
    import sqlite3

    with sqlite3.connect(isolated / "state.sqlite3") as db:
        assert db.execute("SELECT count(*) FROM sessions").fetchone()[0] == 0
    foreign = {**env, "AGENTHUB_HOME": str(a / "other-memory-home")}
    empty = subprocess.run(
        [run.python, "-m", "agent_cli.main", "--json", "memory", "list"],
        env=foreign,
        cwd=b,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert '"enabled":false' in empty.stdout
    assert not os.path.exists(Path(foreign["AGENTHUB_HOME"]))


def test_model_can_copy_host_generated_source_reference(cli_fixture):
    run, project, _, requests = cli_fixture
    run("trust", "add", str(project))
    run("--json", "-p", "MEMORYOBSERVE")
    candidate = json.loads(run("--json", "memory", "candidates").stdout)[0]
    assert candidate["status"] == "ready"
    assert candidate["sources"][0]["sha256"]
    source = json.loads(requests[-2]["messages"][-1]["content"])["result"]["source_ref"]
    assert candidate["sources"][0]["call_id"] == source["call_id"]
