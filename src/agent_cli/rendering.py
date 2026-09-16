"""Independent terminal, plain and JSONL consumers of the public event stream."""

import os
import sys
from dataclasses import asdict

from .presentation import PRIVATE_EVENTS, PresentationState
from .terminal_text import safe_text


class JsonRenderer:
    def __init__(self, redactor, **_):
        self.redactor = redactor

    def event(self, event):
        if event.type not in PRIVATE_EVENTS:
            print(self.redactor.dumps(event), flush=True)

    def result(self, result):
        print(self.redactor.dumps({"type": "result", **asdict(result)}), flush=True)

    def close(self):
        pass


class PlainRenderer:
    def __init__(self, redactor, *, verbose=False, clock=None, **_):
        self.redactor, self.verbose = redactor, verbose
        self.state = PresentationState(redactor, **({"clock": clock} if clock else {}))
        self.printed = {}

    def write_message(self, text):
        print(safe_text(text), flush=True)

    def write_tool(self, tool):
        print(tool.summary(), file=sys.stderr, flush=True)
        lines = tool.output().splitlines()
        selected = lines if self.verbose else lines[:6]
        if selected:
            print("\n".join(selected), file=sys.stderr)
        if len(selected) < len(lines):
            print("… /tools 查看已保存详情", file=sys.stderr)

    def flush_messages(self, force=False):
        for identifier, message in self.state.messages.items():
            offset = self.printed.get(identifier, 0)
            if (force or message.closed) and len(message.text) > offset:
                self.write_message(message.text[offset:])
                self.printed[identifier] = len(message.text)

    def event(self, event):
        for kind, key in self.state.consume(event):
            if kind == "message":
                self.flush_messages()
            elif kind == "flush":
                self.flush_messages(force=True)
            elif kind == "tool":
                self.write_tool(self.state.tools[key])
            elif kind == "phase" and self.verbose:
                print(f"阶段：{safe_text(key)}", file=sys.stderr)

    def result(self, result):
        self.state.finish()
        self.flush_messages(force=True)
        output = safe_text(self.redactor.text(result.output))
        last = next(reversed(self.state.messages.values()), None) if self.state.messages else None
        # Some providers keep one public message ID across tool rounds. Its stream
        # then contains an earlier preamble followed by the final answer.
        if output and (last is None or not last.text.rstrip().endswith(output.strip())):
            self.write_message(output)
        self.close()
        self.write_summary(result)

    def write_summary(self, result):
        usage = result.usage
        text = (f"{result.state.value} · {result.reason_code}\n"
                f"用量 {usage.total_tokens} token · estimated={usage.estimated} · "
                f"incomplete={usage.incomplete} · {result.counters}\n"
                f"Run: {result.run_id}\n继续：agenthub -c；选择其他会话：agenthub -r")
        print(safe_text(self.redactor.text(text)), file=sys.stderr)

    def close(self):
        pass


class TerminalRenderer(PlainRenderer):
    def __init__(self, redactor, *, no_color=False, console=None, **kwargs):
        super().__init__(redactor, **kwargs)
        from rich.console import Console
        from rich.live import Live
        self.console = console or Console(no_color=no_color or bool(os.environ.get("NO_COLOR")))
        self.code_languages = {}
        self.live = Live(self, console=self.console, refresh_per_second=8, transient=True,
                         redirect_stdout=False, redirect_stderr=False)
        self.live.start()

    def __rich__(self):
        from rich.console import Group
        from rich.markdown import Markdown
        from rich.spinner import Spinner
        from rich.text import Text
        pending = "\n".join(m.text[self.printed.get(i, 0):]
                            for i, m in list(self.state.messages.items()))
        # Committed text is in scrollback; only a bounded tail is refreshed.
        budget = max(80, min(800, self.console.width * max(2, self.console.height - 6) // 4))
        pending = pending[-budget:]
        return Group(Markdown(pending), Spinner("dots", text=Text(self.state.status(), style="cyan")))

    def write_message(self, text):
        from rich.markdown import Markdown
        self.console.print(Markdown(safe_text(text)), highlight=False)

    def flush_messages(self, force=False):
        from rich.syntax import Syntax
        # Conservative character limit also accommodates wide CJK characters.
        budget = max(80, min(800, self.console.width * max(2, self.console.height - 6) // 4))
        for identifier, message in self.state.messages.items():
            offset = self.printed.get(identifier, 0)
            pending = message.text[offset:]
            while pending:
                end = len(pending) if force or message.closed else 0
                boundary = pending.find("\n\n")
                if not end and boundary >= 0:
                    end = boundary + 2
                if not end and len(pending) > budget:
                    end = pending.rfind("\n", 0, budget) + 1 or budget
                if not end:
                    break
                chunk = pending[:end]
                # Console.print refreshes Live too. Advance first so it cannot redraw
                # already committed text and inflate the transient area's height.
                offset += end
                self.printed[identifier] = offset
                language = self.code_languages.get(identifier)
                if language is not None and not chunk.lstrip().startswith("```"):
                    self.console.print(Syntax(chunk.split("```")[0], language or "text",
                                              word_wrap=True, background_color="default"))
                    if "```" in chunk:
                        tail = chunk.split("```", 1)[1]
                        if tail.strip():
                            self.write_message(tail)
                else:
                    self.write_message(chunk)
                for line in chunk.splitlines():
                    if line.lstrip().startswith("```"):
                        language = None if language is not None else line.strip()[3:]
                self.code_languages[identifier] = language
                pending = pending[end:]

    def write_tool(self, tool):
        from rich.text import Text
        style = "yellow" if tool.success is None else ("green" if tool.success else "red")
        self.console.print(Text(tool.summary(), style=style))
        lines = tool.output().splitlines()
        selected = lines if self.verbose else lines[:6]
        if selected:
            self.console.print(Text("\n".join(selected)))
        if len(lines) > len(selected):
            self.console.print(Text("… /tools 查看已保存详情", style="dim"))

    def write_summary(self, result):
        from rich.text import Text
        style = {"completed": "green", "cancelled": "yellow"}.get(result.state.value, "red")
        self.console.print(Text(f"{result.state.value} · {result.reason_code}", style=style))
        self.console.print(Text(
            f"{result.usage.total_tokens} token · "
            f"{'估算' if result.usage.estimated else '已确认'}"
            f"{' / 用量不完整' if result.usage.incomplete else ''} · "
            f"模型 {result.counters.get('model_requests', '未知')} · "
            f"工具 {result.counters.get('tool_calls', '未知')} · "
            f"{(result.finished_at - result.started_at).total_seconds():.1f}s", style="dim"
        ))
        self.console.print(Text(f"Run: {result.run_id} · 继续 agenthub -c · 列表 agenthub -r",
                               style="dim"))

    def close(self):
        if self.live:
            self.live.stop()


def renderer_for(redactor, *, json_mode=False, interactive=False, plain=False,
                 no_color=False, verbose=False):
    cls = JsonRenderer if json_mode else (
        TerminalRenderer if interactive and not plain else PlainRenderer
    )
    return cls(redactor, no_color=no_color, verbose=verbose)
