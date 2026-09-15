import asyncio
import codecs
import json
import os
import sys
import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from agent_adapters.local.files import LocalFiles
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.tools import LocalToolExecutor, TrustAuthorization
from agent_adapters.storage.session_lock import SessionBusyError, SessionLock
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import OperationError
from agent_contracts.execution import ExecutionContext, ResourceGrant, WorkspaceSpec
from agent_runtime import AgentConfig, RuntimeEngine, RunRequest, RunState
from agent_runtime.core.ports import ContextConflictError
from agent_runtime.core.run_types import ContextDelta, EventEnvelope
from agent_runtime.runtime.cancellation import CancellationScope, RunLease
from agent_runtime.runtime.run_journal import EventSequenceConflictError
from agent_subsystems.context.local import LocalContextProvider
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.observability.redaction import Redactor, RedactedStream
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.workspaces.paths import canonical_directory
from model_provider.core.interfaces import BaseModelProvider, StreamChunk
from agent_runtime.core.types import ToolCall


@pytest.fixture
def store(tmp_path):
    instance = SQLiteStore(tmp_path / "state.sqlite3")
    yield instance
    instance.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16-le", "utf-16-be"])
async def test_file_edits_preserve_encoding_newlines_and_detect_conflicts(tmp_path, encoding):
    file = tmp_path / "中文 file.txt"
    original = "你好\r\nold\r\n".encode(encoding)
    if encoding.startswith("utf-16"):
        original = (
            codecs.BOM_UTF16_LE if encoding.endswith("le") else codecs.BOM_UTF16_BE
        ) + original
    file.write_bytes(original)
    files = LocalFiles(WorkspaceSpec(tmp_path))
    read = await files.read(str(file))
    await files.edit(str(file), "old", "new", read["sha256"])
    assert file.read_bytes() == original.replace(
        "old".encode(encoding if encoding != "utf-8-sig" else "utf-8"),
        "new".encode(encoding if encoding != "utf-8-sig" else "utf-8"),
    )
    with pytest.raises(OperationError, match="current file"):
        await files.write(str(file), "overwrite", read["sha256"])
    with pytest.raises(OperationError, match="explicitly"):
        await files.read(str(tmp_path.parent / "outside.txt"))


@pytest.mark.asyncio
async def test_context_cas_journal_retries_and_persistence(tmp_path, store):
    before = await store.load("scope")
    snapshot = await store.commit(
        "scope", ContextDelta(0, {}, ({"role": "user", "content": "hello"},))
    )
    assert snapshot.version == 1 and before.version == 0
    with pytest.raises(ContextConflictError):
        await store.commit("scope", ContextDelta(0, {}))
    request = RunRequest("scope", "input", (AgentConfig("a", "A", ""),), SingleAgentPolicy())
    await store.create_run(request, SimpleNamespace(state=RunState.CREATED, started_at=None))
    event = EventEnvelope(request.run_id, "scope", 1, "tool.result", {"value": "ok"})
    await store.append_event(event)
    await store.append_event(event)
    with pytest.raises(EventSequenceConflictError):
        await store.append_event(replace(event, sequence=2))
    await store.recover_session("scope")
    assert store.run_result(request.run_id)["reason_code"] == "process_lost"
    assert (await store.read_events(request.run_id)).terminal
    reopened = SQLiteStore(tmp_path / "state.sqlite3")
    try:
        assert (await reopened.load("scope")).messages[0]["content"] == "hello"
    finally:
        reopened.close()


def test_lock_and_trust_are_independent_per_session(tmp_path, store):
    with SessionLock(tmp_path / "locks", "a"):
        with pytest.raises(SessionBusyError):
            with SessionLock(tmp_path / "locks", "a"):
                pass
        with SessionLock(tmp_path / "locks", "b"):
            pass
    with SessionLock(tmp_path / "locks", "a"):
        pass
    store.trust(tmp_path)
    assert store.is_trusted(tmp_path)
    assert not store.is_trusted(tmp_path.parent)
    store.trust(tmp_path, False)
    assert not store.is_trusted(tmp_path)


def test_stream_redaction_covers_split_secrets():
    stream = RedactedStream(Redactor(["sensitive-key-123"]))
    output = "".join(stream.push(c) for c in "before sensitive-key-123 after") + stream.push(
        "", final=True
    )
    assert output == "before [redacted] after"


class ScriptedModel(BaseModelProvider):
    def __init__(self):
        super().__init__({"model": "deterministic-test"})
        self.inputs = []
        self.round = 0

    async def chat(self, *args, **kwargs):
        raise AssertionError("Streaming path expected")

    async def chat_stream(self, messages, **kwargs):
        self.inputs.append(messages)
        self.round += 1
        if self.round == 1:
            yield StreamChunk(
                tool_call={
                    "id": "write-1",
                    "index": 0,
                    "type": "function",
                    "function": {
                        "name": "file.write",
                        "arguments": json.dumps(
                            {"path": "answer.txt", "content": "42\n", "expected_hash": "new"}
                        ),
                    },
                }
            )
            yield StreamChunk(finish_reason="tool_calls")
        else:
            yield StreamChunk(
                content='Created answer.txt.\n```status_report\n{"state":"completed","will":"complete"}\n```',
                finish_reason="stop",
            )
        yield StreamChunk(usage={"prompt_tokens": 20, "completion_tokens": 10})


@pytest.mark.asyncio
async def test_shared_loop_real_tools_and_restored_context(tmp_path, store):
    root = canonical_directory(tmp_path)
    store.trust(root)
    session = store.new_session(root)
    workspace = WorkspaceSpec(root)
    grant = ResourceGrant(workspace, frozenset({"files", "process"}))
    model = ScriptedModel()
    driver = LocalProcessDriver(Redactor())
    engine = RuntimeEngine(
        agent_executor=AgentLoopExecutor(
            model_provider=model,
            tool_executor=LocalToolExecutor(grant, TrustAuthorization(store), driver, Redactor()),
            context_provider=LocalContextProvider(workspace),
        ),
        context_store=store,
        run_journal=store,
    )
    try:

        async def run(prompt):
            handle = await engine.start(
                RunRequest(
                    session["id"],
                    prompt,
                    (AgentConfig("a", "A", "assistant"),),
                    SingleAgentPolicy(),
                    metadata={"execution_deadline": time.monotonic() + 30},
                )
            )
            return await asyncio.wait_for(handle.result(), 10)

        result = await run("Create a file")
        assert result.state == RunState.COMPLETED
        assert (tmp_path / "answer.txt").read_text() == "42\n"
        assert result.usage.prompt_tokens == 40 and not result.usage.estimated
        tool_message = next(m for m in model.inputs[1] if m.role == "tool")
        assert json.loads(tool_message.content)["success"] is True
        await run("What did you create?")
        history = model.inputs[-1]
        assert sum(m.role == "user" and m.content == "Create a file" for m in history) == 1
        assert any(m.role == "assistant" and "answer.txt" in m.content for m in history)
    finally:
        await engine.shutdown()
        await driver.aclose()


@pytest.mark.asyncio
async def test_windows_process_exit_output_timeout_and_cleanup(tmp_path):
    context = ExecutionContext(
        "r",
        "s",
        "a",
        ResourceGrant(WorkspaceSpec(tmp_path), frozenset()),
        time.monotonic() + 30,
        CancellationScope(),
        RunLease("r"),
    )
    driver = LocalProcessDriver(Redactor(["very-secret-value"]))
    try:
        result = await driver.run(
            [sys.executable, "-c", "print('very-secret-value'); print('x'*80000)"],
            tmp_path,
            timeout=10,
            context=context,
        )
        assert result["exit_code"] == 0
        assert "very-secret-value" not in result["stdout"]
        assert result["truncated"] and len(result["stdout"].encode()) <= 65536
        with pytest.raises(OperationError, match="deadline"):
            await driver.run(
                [sys.executable, "-c", "import time; time.sleep(20)"],
                tmp_path,
                timeout=0.15,
                context=context,
            )
        assert not driver.jobs and not driver.processes
    finally:
        await driver.aclose()


@pytest.mark.asyncio
async def test_powershell_git_authorization_and_nonzero_exit(tmp_path, store):
    root = canonical_directory(tmp_path)
    store.trust(root)
    redactor = Redactor()
    driver = LocalProcessDriver(redactor)
    executor = LocalToolExecutor(
        ResourceGrant(WorkspaceSpec(root), frozenset({"files", "process"})),
        TrustAuthorization(store),
        driver,
        redactor,
    ).bind_execution(
        SimpleNamespace(
            run_id="r",
            context_scope_id="s",
            agent=SimpleNamespace(id="a"),
            metadata={"execution_deadline": time.monotonic() + 30},
        ),
        CancellationScope(),
        RunLease("r"),
    )
    try:
        result = await executor.execute(
            ToolCall(
                "powershell.run",
                {"script": "1..3 | ForEach-Object { $_ * 2 }; Write-Output '中文'"},
                "p",
            )
        )
        assert result.success, result
        assert "中文" in result.result["stdout"] and "6" in result.result["stdout"]
        result = await executor.execute(ToolCall("powershell.run", {"script": "exit 7"}, "e"))
        assert not result.success and result.result["exit_code"] == 7
        result = await executor.execute(ToolCall("git.run", {"args": ["init", "--quiet"]}, "g"))
        assert result.success, result
        assert (tmp_path / ".git").is_dir()
        store.trust(root, False)
        result = await executor.execute(
            ToolCall("powershell.run", {"script": "'not executed'"}, "d")
        )
        assert not result.success and result.result["error_code"] == "authorization_required"
    finally:
        await driver.aclose()


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI")
def test_dpapi_credential_roundtrip_and_path_validation(tmp_path):
    from agent_adapters.credentials.local import LocalCredentialStore
    from agent_contracts.errors import ConfigurationError

    credentials = LocalCredentialStore(tmp_path)
    reference = credentials.save("test-secret-only")
    assert credentials.resolve(reference) == "test-secret-only"
    assert all(b"test-secret-only" not in p.read_bytes() for p in tmp_path.iterdir())
    with pytest.raises(ConfigurationError):
        credentials.resolve("dpapi:../../anything")


@pytest.mark.asyncio
async def test_failed_agent_report_is_a_failed_kernel_run(store):
    from agent_runtime.core.run_types import AgentExecutionResult
    from agent_runtime.core.types import AgentReport, AgentState, AgentWill

    class FailedExecutor:
        async def execute(self, request, **kwargs):
            return AgentExecutionResult(
                request.agent.id,
                AgentReport(request.agent.id, AgentState.FAILED, AgentWill.BLOCKED),
                output="Cannot complete",
            )

    engine = RuntimeEngine(agent_executor=FailedExecutor(), context_store=store, run_journal=store)
    try:
        handle = await engine.start(
            RunRequest("failure", "task", (AgentConfig("a", "A", ""),), SingleAgentPolicy())
        )
        result = await handle.result()
        assert result.state == RunState.FAILED and result.reason_code == "agent_blocked"
        assert result.output == "Cannot complete"
        assert not (await store.load("failure")).messages
    finally:
        await engine.shutdown()
