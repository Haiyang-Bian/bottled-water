"""Working context stays bounded while saved observations remain queryable."""

import json
from dataclasses import replace

import pytest

from agent_contracts.context import ContextBudget
from agent_contracts.errors import OperationError
from agent_contracts.harness import ExecutionStopped
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_runtime.core.types import ToolResult
from agent_runtime.runtime.run_journal import InMemoryRunJournal
from agent_subsystems.context.assembler import ContextAssembler
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.tools.history import JournalResultReader
from model_provider import ChatMessage, StreamChunk


def test_history_is_dropped_by_whole_turn_and_tool_pairs_remain_complete():
    old = [ChatMessage("user", "old?" * 1000), ChatMessage("assistant", "old answer" * 1000)]
    current = ChatMessage("user", "Fix the bug and report the actual test result.")
    call = {"id": "c", "type": "function", "function": {"name": "file.read", "arguments": "{}"}}
    assistant = ChatMessage("assistant", "", tool_calls=[call])
    result = ChatMessage(
        "tool",
        json.dumps(
            {
                "success": True,
                "result": {"path": "calc.py", "sha256": "a" * 64, "content": "data" * 10000},
            }
        ),
        tool_call_id="c",
    )
    messages, info = ContextAssembler(ContextBudget(2200)).prepare(
        [*old, current, assistant, result], "system", [], current_request=current, run_id="run"
    )
    assert messages[0] is current
    assert [m.role for m in messages] == ["user", "assistant", "tool"]
    assert messages[1].tool_calls[0]["id"] == messages[2].tool_call_id == "c"
    assert info["after_chars"] <= 2200 and info["dropped_turns"] == 1
    summary = json.loads(messages[-1].content)
    assert summary["result"]["sha256"] == "a" * 64
    assert summary["result_ref"] == {
        "run_id": "run",
        "call_id": "c",
        "tool": "run.read_tool_result",
    }
    assert len(result.content) > 40000  # Original journal material is unchanged.


def test_current_request_and_tool_schemas_are_not_silently_truncated():
    current = ChatMessage("user", "important request " * 100)
    with pytest.raises(ExecutionStopped, match="context_budget_exhausted"):
        ContextAssembler(ContextBudget(500)).prepare(
            [current], "", [], current_request=current, run_id="r"
        )
    with pytest.raises(ExecutionStopped, match="context_budget_exhausted"):
        ContextAssembler(ContextBudget(2500)).prepare(
            [current], "", [{"schema": "x" * 3000}], current_request=current, run_id="r"
        )
    assert current.content == "important request " * 100


def test_token_window_reserves_output_and_remains_an_explicit_estimate():
    current = ChatMessage("user", "request")
    assembler = ContextAssembler(ContextBudget(64000, 100, 30), lambda text: len(text))
    with pytest.raises(ExecutionStopped, match="context_budget_exhausted"):
        assembler.prepare([current], "system", [], current_request=current, run_id="r")
    _, info = ContextAssembler().prepare([current], "", [], current_request=current, run_id="r")
    assert info["context_window_tokens"] is None and info["token_count_estimated"]


class LargeToolModel:
    def __init__(self):
        self.calls = 0
        self.inputs = []

    async def chat_stream(self, messages, **kwargs):
        self.calls += 1
        self.inputs.append(messages)
        if self.calls == 1:
            yield StreamChunk(
                tool_call={
                    "index": 0,
                    "id": "original",
                    "type": "function",
                    "function": {"name": "file.read", "arguments": "{}"},
                }
            )
        elif self.calls == 2:
            summary = json.loads(messages[-1].content)
            assert summary["context_summary"]
            reference = summary["result_ref"]
            yield StreamChunk(
                tool_call={
                    "index": 0,
                    "id": "retrieve",
                    "type": "function",
                    "function": {
                        "name": "run.read_tool_result",
                        "arguments": json.dumps(
                            {
                                "run_id": reference["run_id"],
                                "call_id": reference["call_id"],
                                "offset": 0,
                                "limit": 100,
                            }
                        ),
                    },
                }
            )
        else:
            result = json.loads(messages[-1].content)["result"]
            assert result["next_offset"] == 100 and result["record_truncated"]
            yield StreamChunk(content="Verified the saved record.")
        yield StreamChunk(
            usage={
                "prompt_tokens": 20,
                "completion_tokens": 3,
                "prompt_tokens_details": {"cached_tokens": 7},
            },
            finish_reason="stop",
        )


class LargeTool:
    async def list_tools(self):
        return [{"type": "function", "function": {"name": "file.read"}}]

    async def execute(self, call):
        return ToolResult(
            call.call_id,
            True,
            {
                "path": "source.py",
                "sha256": "b" * 64,
                "content": "source" * 8000,
                "truncated": True,
            },
        )


async def test_full_loop_compacts_then_reads_saved_result_and_preserves_cache_usage():
    model, journal = LargeToolModel(), InMemoryRunJournal()
    engine = RuntimeEngine(
        run_journal=journal,
        agent_executor=AgentLoopExecutor(
            model_provider=model,
            tool_executor=LargeTool(),
            run_journal=journal,
            context_budget=ContextBudget(3800),
        ),
    )
    handle = await engine.start(
        RunRequest("scope", "Inspect source", (AgentConfig("a", "A", ""),), SingleAgentPolicy())
    )
    result = await handle.result()
    assert result.state.value == "completed"
    assert result.usage.total_tokens == 69
    assert result.usage.cached_prompt_tokens == 21
    assert not result.usage.cache_usage_incomplete
    original = await JournalResultReader(journal, "scope").read(handle.run_id, "original")
    assert "source" in original["result_json"]
    assert original["record_truncated"]
    with pytest.raises(OperationError, match="No accessible"):
        await JournalResultReader(journal, "another-scope").read(handle.run_id, "original")
    events = [event async for event in handle.events()]
    budgets = [e.payload for e in events if e.type == "agent.context_budget"]
    assert any(b["compacted_results"] for b in budgets)
    assert all(b["after_chars"] <= 3800 for b in budgets)
    await engine.shutdown()


def test_cache_subtotals_do_not_inflate_total_usage():
    from agent_runtime.core.run_types import Usage

    usage = Usage(100, 10, cached_prompt_tokens=80)
    usage.add(Usage(50, 5, cache_usage_incomplete=True))
    assert usage.total_tokens == 165
    assert usage.cached_prompt_tokens == 80 and usage.cache_usage_incomplete
    assert replace(usage).to_dict() == usage.to_dict()
