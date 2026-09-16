"""Interpret public events without affecting the Runtime or performing terminal IO."""

import json
import time
from dataclasses import dataclass, field

from .terminal_text import TerminalTextStream, safe_text

PRIVATE_EVENTS = {"agent.thinking", "model.reasoning", "model.thinking"}


@dataclass
class Message:
    stream: TerminalTextStream
    text: str = ""
    closed: bool = False


@dataclass
class ToolView:
    call_id: str
    name: str = "未知"
    arguments: dict = field(default_factory=dict)
    started: float | None = None
    elapsed: float | None = None
    success: bool | None = None
    result: object = None
    error: str = ""

    def target(self):
        args = self.arguments
        value = args.get("path") or args.get("cwd") or args.get("pattern") or ""
        command = args.get("script") or args.get("args")
        if args.get("executable"):
            command = f"{args['executable']} {args.get('args', [])}"
        if command:
            value = f"{value} · {command}"
        return " ".join(safe_text(value).split())[:160]

    def summary(self):
        state = "结果未知" if self.success is None else ("成功" if self.success else "失败")
        fields = [self.name, state, self.target()]
        if self.elapsed is not None:
            fields.append(f"{self.elapsed:.1f}s")
        if isinstance(self.result, dict):
            for key in ("path", "start_line", "end_line", "exit_code", "total_matches",
                        "count", "scope", "next_offset", "sha256", "conflict", "executable"):
                if key in self.result:
                    fields.append(f"{key}={self.result[key]}")
            if self.result.get("truncated"):
                fields.append("结果已截断，保存内容不完整")
        return safe_text(" · ".join(str(f) for f in fields if f))

    def output(self):
        if isinstance(self.result, dict):
            values = [str(self.result[k]) for k in ("stdout", "stderr") if self.result.get(k)]
        else:
            values = [str(self.result)] if self.result is not None else []
        if self.error:
            values.insert(0, self.error)
        return safe_text("\n".join(values))


class PresentationState:
    def __init__(self, redactor, clock=time.monotonic):
        self.redactor, self.clock = redactor, clock
        self.messages = {}
        self.tools = {}
        self.phase = "准备"
        self.active_tool = None
        self.started = clock()
        self.counters = {"model_requests": 0, "tool_rounds": 0, "tool_calls": 0}
        self.seen = set()

    def consume(self, event):
        """Return display actions: (message|tool|phase, key)."""
        key = (event.run_id, event.sequence)
        if key in self.seen or event.type in PRIVATE_EVENTS:
            return []
        self.seen.add(key)
        kind, p = event.type, self.redactor.value(event.payload)
        actions = []
        if kind in {"message_start", "agent.token", "message_stop"}:
            identifier = p.get("agent_message_id", "legacy")
            message = self.messages.setdefault(identifier, Message(TerminalTextStream(self.redactor)))
            if kind == "agent.token":
                message.text += message.stream.push(p.get("token", ""))
                actions.append(("message", identifier))
            elif kind == "message_stop":
                message.text += message.stream.push("", final=True)
                message.closed = True
                actions.append(("message", identifier))
        elif kind == "agent.tool_call":
            self.counters["tool_rounds"] += 1
            for call in p.get("calls", []):
                function = call.get("function", {})
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                identifier = call.get("id", "unknown")
                self.tools[identifier] = ToolView(
                    identifier, function.get("name", "未知"),
                    arguments if isinstance(arguments, dict) else {},
                )
            actions.append(("flush", ""))
        elif kind in {"agent.tool_started", "agent.tool_result"}:
            identifier = p.get("call_id", "unknown")
            tool = self.tools.setdefault(identifier, ToolView(identifier))
            tool.name = p.get("tool", tool.name)
            if kind == "agent.tool_started":
                tool.started = self.clock()
                self.active_tool = identifier
                self.counters["tool_calls"] += 1
                actions.append(("flush", ""))
            else:
                tool.elapsed = self.clock() - tool.started if tool.started is not None else None
                tool.success = p.get("success")
                tool.result, tool.error = p.get("result"), p.get("error") or ""
                self.active_tool = None
                actions.append(("tool", identifier))
        elif kind == "execution.phase_started":
            self.phase = p.get("phase", "未知")
            if self.phase == "model":
                self.counters["model_requests"] += 1
            actions.append(("phase", self.phase))
        elif kind == "execution.phase_finished":
            actions.append(("phase", f"{p.get('phase', '未知')} 结束"))
            self.phase = "协调"
        return actions

    def finish(self):
        for message in self.messages.values():
            if not message.closed:
                message.text += message.stream.push("", final=True)
                message.closed = True

    def status(self):
        name = {"model": "等待模型", "context": "整理上下文", "tool": "执行工具"}.get(
            self.phase, self.phase
        )
        if self.active_tool:
            tool = self.tools[self.active_tool]
            elapsed = self.clock() - tool.started if tool.started is not None else 0
            name = f"{tool.name} {tool.target()} · 工具 {elapsed:.1f}s"
        return (f"{name} · 已耗 {self.clock() - self.started:.1f}s · "
                f"模型 {self.counters['model_requests']} · 工具 {self.counters['tool_calls']}")
