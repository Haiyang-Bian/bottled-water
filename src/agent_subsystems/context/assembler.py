"""Deterministic working-context compaction; the durable journal is never rewritten."""

import json
import math
from dataclasses import asdict, replace

from agent_contracts.context import ContextBudget
from agent_contracts.harness import ExecutionStopped


def serialized(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


class ContextAssembler:
    def __init__(self, budget=None, token_counter=None):
        self.budget = budget or ContextBudget()
        self.token_counter = token_counter

    def prepare(self, messages, system_prompt, tools, *, current_request, run_id):
        working = list(messages)

        def measure():
            payload = serialized(
                {"system": system_prompt, "messages": [asdict(m) for m in working], "tools": tools}
            )
            estimate = (
                self.token_counter(payload)
                if self.token_counter
                else math.ceil(len(payload.encode("utf-8")) / 3)
            )
            return len(payload), max(0, int(estimate))

        def fits():
            chars, tokens = measure()
            return chars <= self.budget.max_context_chars and (
                self.budget.context_window_tokens is None
                or tokens + self.budget.output_reserve_tokens <= self.budget.context_window_tokens
            )

        before_chars, before_tokens = measure()
        dropped_turns = dropped_messages = compacted_results = 0
        while not fits():
            current_index = next(i for i, m in enumerate(working) if m is current_request)
            if current_index == 0:
                break
            end = next(
                (i for i in range(1, current_index) if working[i].role == "user"), current_index
            )
            working = working[end:]
            dropped_turns += 1
            dropped_messages += end

        for index, message in enumerate(working):
            if fits():
                break
            if message.role != "tool":
                continue
            summary = summarize_tool_result(message.content, run_id, message.tool_call_id)
            if len(summary) < len(message.content):
                working[index] = replace(message, content=summary)
                compacted_results += 1

        if not fits():
            raise ExecutionStopped("context_budget_exhausted")
        after_chars, after_tokens = measure()
        return working, {
            "before_chars": before_chars,
            "after_chars": after_chars,
            "before_tokens_estimated": before_tokens,
            "after_tokens_estimated": after_tokens,
            "dropped_turns": dropped_turns,
            "dropped_messages": dropped_messages,
            "compacted_results": compacted_results,
            "max_context_chars": self.budget.max_context_chars,
            "context_window_tokens": self.budget.context_window_tokens,
            "token_count_estimated": True,
            "output_reserve_tokens": self.budget.output_reserve_tokens,
        }


def summarize_tool_result(content, run_id, call_id):
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        data = {"result": content}
    if not isinstance(data, dict):
        data = {"result": data}
    if data.get("context_summary"):
        return content
    result = data.get("result")
    summary = {}
    if isinstance(result, dict):
        for key in (
            "path",
            "sha256",
            "exit_code",
            "error_code",
            "bytes_written",
            "start_line",
            "total_lines",
            "next_offset",
        ):
            if key in result:
                value = result[key]
                summary[key] = value[:512] if isinstance(value, str) else value
        for key, limit in (("stdout", 300), ("stderr", 500), ("content", 300)):
            if result.get(key):
                summary[key + "_excerpt"] = str(result[key])[-limit:]
    else:
        summary["excerpt"] = str(result)[:300]
    return serialized(
        {
            "context_summary": True,
            "success": data.get("success"),
            "result": summary,
            "error": str(data.get("error") or "")[:512],
            "truncated": True,
            "record_truncated": bool(isinstance(result, dict) and result.get("truncated")),
            "result_ref": {"run_id": run_id, "call_id": call_id, "tool": "run.read_tool_result"},
            "notice": "Working-context excerpt. Read the saved record for details; it may also be truncated.",
        }
    )
