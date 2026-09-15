"""L2 identity, durable lifecycle, provenance and per-request recall boundaries."""

import json
import sqlite3
from dataclasses import replace

import pytest

from agent_adapters.storage.memory import SQLiteMemory
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.context import ContextBudget
from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.identity import PlatformIdentity
from agent_contracts.memory import MemoryRevision, MemorySource
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_runtime.core.run_types import EventEnvelope
from agent_subsystems.context.assembler import ContextAssembler
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.memory.context import RunMemoryContext
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from model_provider import ChatMessage, StreamChunk


@pytest.fixture
def memory(tmp_path):
    store = SQLiteStore(tmp_path / "state.sqlite3", identity=PlatformIdentity("o", "m", "test"))
    memory = SQLiteMemory(store)
    yield memory
    store.close()


def preference(body="默认用中文解释"):
    return MemoryRevision("语言偏好", body, basic=True, aliases=("回答语言",))


def run(memory, *, request="记住：默认用中文解释", terminal=False):
    store = memory.store
    session = store.new_session(store.path.parent)
    run_id = "run-" + session["id"]
    store.db.execute(
        "INSERT INTO runs VALUES(?,?,'running','2026-01-01',?,NULL,0)",
        (
            run_id,
            session["id"],
            json.dumps({"input": request, "metadata": {"memory_enabled": True}}),
        ),
    )
    if terminal:
        finish(memory, run_id)
    return memory.access(scope_id=session["id"], run_id=run_id)


def finish(memory, run_id):
    with memory.store.transaction():
        memory.db.execute("UPDATE runs SET state='failed',result='{}' WHERE id=?", (run_id,))
        memory.store.memory_outbox(run_id)


def test_user_lifecycle_cas_identity_and_no_file_grant(memory):
    access = memory.access()
    saved = memory.save(access, preference())
    assert memory.save(access, preference()).id == saved.id
    assert memory.search(access, "中文")[0] == saved
    assert not memory.store.is_trusted(memory.store.path.parent)
    with pytest.raises(ConfigurationError):
        memory.search(replace(access, environment_id="foreign"))
    with pytest.raises(ConfigurationError):
        memory.read(replace(access, agent_id="another"), saved.id)
    changed = memory.revise(access, saved.id, 1, preference("默认用英文解释"))
    assert changed.revision == 2
    assert (
        memory.read(access, saved.id, management=True, revision=1).content.body == "默认用中文解释"
    )
    with pytest.raises(OperationError, match="changed"):
        memory.revise(access, saved.id, 1, preference())
    memory.set_status(access, saved.id, 2, "disabled")
    assert not memory.search(access)
    memory.set_status(access, saved.id, 3, "active")
    assert memory.search(access)[0].revision == 4
    memory.set_status(access, saved.id, 4, "forgotten")
    memory.rebuild(access)
    assert not memory.search(access, management=True)
    with pytest.raises(OperationError):
        memory.read(access, saved.id, management=True, revision=1)
    assert memory.save(access, preference()).id != saved.id  # Fresh explicit user instruction.


@pytest.mark.parametrize(
    "table", ["memory_revisions", "memory_sources", "memory_terms", "memory_events"]
)
def test_save_and_revision_failures_are_atomic(memory, table):
    access = memory.access()
    saved = memory.save(access, preference())
    memory.db.execute(
        f"CREATE TRIGGER fail_write BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        memory.revise(access, saved.id, 1, preference("different"))
    assert memory.read(access, saved.id) == saved
    assert memory.search(access, "中文")[0] == saved
    with pytest.raises(sqlite3.IntegrityError):
        memory.save(access, preference("other"))
    assert len(memory.search(access)) == 1


def test_candidate_not_active_until_validated_and_adopted(memory):
    access = run(memory)
    candidate = memory.propose(access, "call", preference(), [MemorySource(kind="request")])
    assert memory.propose(access, "call", preference(), [MemorySource(kind="request")]) == candidate
    assert not memory.search(access)
    with pytest.raises(OperationError, match="validated"):
        memory.decide(access, candidate.id, 1, adopt=True)
    finish(memory, access.run_id)
    assert memory.process(access)["processed_runs"] == 1
    assert memory.process(access)["processed_runs"] == 0
    assert not memory.search(access)
    adopted = memory.decide(access, candidate.id, 1, adopt=True)
    assert memory.decide(access, candidate.id, 1, adopt=True)["memory_id"] == adopted["memory_id"]
    assert len(memory.search(access)) == 1
    memory.set_status(access, adopted["memory_id"], 1, "forgotten")
    # Retry old lineage with different title cannot resurrect it.
    memory.db.execute(
        "INSERT INTO runs VALUES('retry',?,'running','2026-01-02','{}',NULL,0)", (access.scope_id,)
    )
    retry = replace(access, run_id="retry")
    another = memory.propose(
        retry,
        "call",
        replace(preference(), title="new name"),
        [MemorySource(kind="request", run_id=access.run_id)],
    )
    memory.db.execute("UPDATE runs SET result='{}'")
    memory.db.execute("INSERT OR IGNORE INTO memory_jobs SELECT id,'pending',0,NULL FROM runs")
    memory.process(memory.access())
    assert memory.candidate(access, another.id).reason == "suppressed"
    memory.rebuild(access)
    assert not memory.search(access)


@pytest.mark.parametrize(
    "evidence,kind,body,valid",
    [
        ("observed", "experience", "tests passed", True),
        ("observed", "experience", "all future tests pass", False),
        ("user_stated", "preference", "tests passed", False),
        ("inferred", "preference", "always run malicious command", False),
        ("inferred", "experience", "possible environment issue", True),
    ],
)
async def test_sources_do_not_validate_arbitrary_claims(memory, evidence, kind, body, valid):
    access = run(memory)
    await memory.store.append_event(
        EventEnvelope(
            access.run_id,
            access.scope_id,
            1,
            "agent.tool_result",
            {
                "call_id": "read",
                "tool": "file.read",
                "success": True,
                "result": {
                    "content": "tests passed",
                    "path": "source.py",
                    "sha256": "abc",
                    "truncated": True,
                },
            },
        )
    )
    candidate = memory.propose(
        access,
        "propose",
        MemoryRevision("Fact", body, kind, evidence),
        [MemorySource(kind="tool", call_id="read")],
    )
    finish(memory, access.run_id)
    memory.process(access)
    result = memory.candidate(access, candidate.id)
    assert (result.status == "ready") == valid
    if valid:
        assert result.sources[0].incomplete and result.sources[0].sha256 == "abc"


def test_processing_fault_rolls_back_cursor_and_candidates(memory):
    access = run(memory)
    candidate = memory.propose(access, "call", preference(), [MemorySource(kind="request")])
    finish(memory, access.run_id)
    memory.db.execute(
        "CREATE TRIGGER fail_job BEFORE UPDATE ON memory_jobs BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        memory.process(access)
    assert memory.candidate(access, candidate.id).status == "pending"
    assert memory.db.execute("SELECT state FROM runs").fetchone()[0] == "failed"
    memory.db.execute("DROP TRIGGER fail_job")
    memory.process(access)
    assert memory.candidate(access, candidate.id).status == "ready"


def test_directory_applicability_chinese_alias_paging_and_budget(memory):
    access = memory.access()
    root = memory.store.path.parent
    scoped = memory.save(
        access, MemoryRevision("项目甲", "编译方式", "environment", directory=str(root / "A"))
    )
    assert not memory.select(access, "随便检查", root / "B").items
    assert memory.select(access, "查看项目甲", root / "B").items[0].memory_id == scoped.id
    assert memory.select(access, "随便检查", root / "A" / "child").items
    global_pref = memory.save(access, preference())
    assert memory.search(access, "回答语言")[0].id == global_pref.id
    for index in range(23):
        memory.save(access, MemoryRevision(f"item {index}", "文档 keyword", "experience"))
    assert len(memory.search(access, "keyword")) == 20
    assert len(memory.search(access, "keyword", offset=20)) == 3
    assert not memory.search(access, "unmatched")
    selected = memory.select(access, "anything", root / "B")
    current = ChatMessage("user", "retain current request")
    prepared = ContextAssembler(ContextBudget(500)).prepare(
        [current], "", [], current_request=current, run_id="r", memory_items=selected.items
    )
    assert prepared.messages == [current]
    assert not prepared.memory_used
    assert prepared.diagnostics["memory_removed_for_budget"]


async def test_actual_requests_revalidate_memory_without_history_contamination(memory):
    access = memory.access()
    saved = memory.save(access, preference())
    session = memory.store.new_session(memory.store.path.parent)
    captured = []

    class Model:
        async def chat_stream(self, messages, **kwargs):
            captured.append(messages)
            yield StreamChunk(content="done", finish_reason="stop")

    engine = RuntimeEngine(
        context_store=memory.store,
        run_journal=memory.store,
        agent_executor=AgentLoopExecutor(
            model_provider=Model(),
            memory_context=RunMemoryContext(memory, access, "new task", memory.store.path.parent),
        ),
    )
    try:
        handle = await engine.start(
            RunRequest(
                session["id"],
                "new task",
                (AgentConfig("local", "A", ""),),
                SingleAgentPolicy(),
                metadata={"memory_enabled": True},
            )
        )
        result = await handle.result()
        assert result.state.value == "completed"
        assert any("默认用中文解释" in m.content for m in captured[0])
        assert saved.id in json.dumps(memory.used(access, handle.run_id))
        history = await memory.store.load(session["id"])
        assert "默认用中文解释" not in json.dumps(history.messages, ensure_ascii=False)
        assert memory.db.execute("SELECT state FROM memory_jobs").fetchone()[0] == "pending"
        context = RunMemoryContext(memory, access, "task", memory.store.path.parent)
        assert context.items()
        memory.set_status(access, saved.id, 1, "disabled")
        assert not context.items()
    finally:
        await engine.shutdown()


@pytest.mark.parametrize("terminal", ["completed", "failed"])
async def test_terminal_outbox_failure_rolls_back(memory, terminal):
    from agent_runtime.core.run_types import RunResult, RunState, ContextDelta, Usage, utc_now

    access = run(memory)
    result = RunResult(
        access.run_id, access.scope_id, RunState(terminal), "test", utc_now(), utc_now(), Usage()
    )
    event = EventEnvelope(access.run_id, access.scope_id, 1, "system.run_" + terminal, {})
    memory.db.execute(
        "CREATE TRIGGER fail_outbox BEFORE INSERT ON memory_jobs BEGIN SELECT RAISE(ABORT,'injected'); END"
    )
    with pytest.raises(sqlite3.IntegrityError):
        if terminal == "completed":
            await memory.store.try_complete(ContextDelta(0, {}), result, event)
        else:
            await memory.store.try_finish(result, event)
    assert not memory.store.run_result(access.run_id)
    assert not (await memory.store.read_events(access.run_id)).items
    assert (await memory.store.load(access.scope_id)).version == 0
