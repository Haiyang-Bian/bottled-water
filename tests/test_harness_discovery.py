"""Search exclusions affect discovery, never resource authorization."""

from pathlib import Path

import pytest

from agent_adapters.local.files import LocalFiles
from agent_contracts.execution import ExecutionLocation, WorkspaceSpec
from agent_contracts.errors import OperationError
from agent_subsystems.workspaces.paths import canonical_directory


def write(root, relative, text="needle"):
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


async def test_shallow_listing_and_recursive_source_discovery(tmp_path):
    root = canonical_directory(tmp_path)
    for folder in (".next", "target", "node_modules"):
        for index in range(80):
            write(root, f"{folder}/{index}.json")
    write(root, "源码 文件/app.py")
    write(root, "README.md")
    files = LocalFiles(WorkspaceSpec((root,)), ExecutionLocation(root))
    shallow = await files.list()
    assert [p.lower() for p in shallow["files"]] == ["readme.md"]
    assert shallow["directories"] == ["源码 文件"]
    assert shallow["discovery"]["ignored_directories"] == 3
    recursive = await files.list(recursive=True)
    assert "源码 文件/app.py" in recursive["files"]
    assert len((await files.search("needle"))["matches"]) == 2
    included = await files.list(path=".next", include_ignored=True)
    assert len(included["files"]) == 80
    assert (await files.read(".next/0.json"))["content"] == "needle"


async def test_nested_ignore_negation_and_tracked_generated_files(tmp_path):
    root = canonical_directory(tmp_path)
    write(root, ".gitignore", "*.log\n")
    write(root, "src/.gitignore", "!keep.log\nprivate/\n")
    for name in (
        "src/keep.log",
        "src/drop.log",
        "src/private/hidden.py",
        "target/tracked.py",
        "target/untracked.py",
    ):
        write(root, name)

    async def index(directory):
        return ["target/tracked.py"], "complete"

    files = LocalFiles(WorkspaceSpec((root,)), ExecutionLocation(root), index_reader=index)
    names = (await files.list(recursive=True))["files"]
    assert "src/keep.log" in names and "target/tracked.py" in names
    assert not {"src/drop.log", "src/private/hidden.py", "target/untracked.py"} & set(names)
    nested = (await files.list("src", recursive=True))["files"]
    assert "keep.log" in nested and "drop.log" not in nested


async def test_pagination_and_include_ignored_preserve_authorization(tmp_path):
    root = canonical_directory(tmp_path)
    for index in range(9):
        write(root, f"src/{index}.py")
    files = LocalFiles(WorkspaceSpec((root,)), ExecutionLocation(root))
    offset = 0
    names = []
    while True:
        page = await files.list(recursive=True, limit=3, offset=offset)
        names.extend(page["files"] + page["directories"])
        if page["next_offset"] is None:
            break
        offset = page["next_offset"]
    assert len(names) == len(set(names)) == 10
    with pytest.raises(OperationError, match="Add this directory"):
        await files.list(str(Path(root).parent), include_ignored=True)


async def test_failed_index_read_is_visible_in_discovery_diagnostics(tmp_path):
    root = canonical_directory(tmp_path)

    async def broken(directory):
        raise OSError("index unavailable")

    write(root, "main.py")
    result = await LocalFiles(WorkspaceSpec((root,)), ExecutionLocation(root), index_reader=broken).list()
    assert result["files"] == ["main.py"]
    assert result["discovery"]["discovery_degraded"]
    assert result["discovery"]["index_state"] == "failed"


async def test_real_git_index_preserves_tracked_source_in_generated_directory(tmp_path):
    import subprocess
    import time
    from agent_adapters.local.processes import LocalProcessDriver
    from agent_adapters.local.tools import LocalToolExecutor
    from agent_contracts.execution import ResourceGrant
    from agent_runtime.core.run_types import AgentExecutionRequest, ContextSnapshot
    from agent_runtime.core.types import AgentConfig, ToolCall
    from agent_runtime.runtime.cancellation import CancellationScope, RunLease
    from agent_subsystems.observability.redaction import Redactor

    root = canonical_directory(tmp_path)
    write(root, "target/tracked.py")
    write(root, "target/generated.py")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True, timeout=10)
    subprocess.run(
        ["git", "add", "target/tracked.py"], cwd=root, check=True, capture_output=True, timeout=10
    )

    class Allow:
        def authorize(self, spec, context):
            return "allow"

    redactor = Redactor()
    driver = LocalProcessDriver(redactor)
    try:
        request = AgentExecutionRequest(
            "run",
            "scope",
            AgentConfig("a", "A", ""),
            "",
            "",
            ContextSnapshot("scope"),
            1000,
            metadata={"execution_deadline": time.monotonic() + 20},
        )
        executor = LocalToolExecutor(
            ResourceGrant(WorkspaceSpec((root,)), frozenset({"files"})), ExecutionLocation(root), Allow(), driver, redactor
        ).bind_execution(request, CancellationScope(), RunLease("run"))
        call, error = ToolCall.new(
            {"id": "c", "function": {"name": "file.list", "arguments": '{"recursive":true}'}}
        )
        assert error is None
        result = await executor.execute(call)
        assert result.success
        assert result.result["discovery"]["index_state"] == "complete"
        assert "target/tracked.py" in result.result["files"]
        assert "target/generated.py" not in result.result["files"]
    finally:
        await driver.aclose()
