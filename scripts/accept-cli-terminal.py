"""Real ConPTY keyboard/resize capture, with deterministic HTTP model responses.

Requires pywinpty, pyte and Pillow in the validation environment only. PNGs render
the captured VT cell buffer; they are not desktop screenshots or model mockups.
"""

import argparse
import contextlib
import json
import os
from pathlib import Path
import select
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests"))


class Terminal:
    def __init__(self, argv, cwd, env, output):
        import pyte
        from winpty import PtyProcess
        self.output = output
        env = {**env, "TERM": "xterm-256color", "COLORTERM": "truecolor"}
        env.pop("NO_COLOR", None)
        # String "0" avoids PtyProcess.spawn's truthy-default handling of integer zero.
        self.proc = PtyProcess.spawn(argv, cwd=str(cwd), env=env, dimensions=(32, 100), backend="0")
        self.screen = pyte.HistoryScreen(100, 32, history=5000)
        self.stream = pyte.Stream(self.screen)
        self.raw = ""

    def pump(self, duration=0.15):
        if select.select([self.proc.fileobj], [], [], duration)[0]:
            try:
                value = self.proc.read(65536)
            except EOFError:
                return
            self.raw += value
            self.stream.feed(value)
            if "\x1b[6n" in value:
                self.proc.write(f"\x1b[{self.screen.cursor.y + 1};{self.screen.cursor.x + 1}R")

    def wait(self, text, *, after=0, timeout=60):
        end = time.monotonic() + timeout
        while text not in self.raw[after:]:
            self.pump()
            if time.monotonic() > end or not self.proc.isalive():
                raise AssertionError(f"Missing {text!r}: {self.raw[-2000:]!r}")
        for _ in range(3):
            self.pump(0.1)

    def send(self, text):
        mark = len(self.raw)
        self.proc.write(text)
        return mark

    def snapshot(self, name):
        from PIL import Image, ImageDraw, ImageFont
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 17)
        colors = {"default": "#d4d4d4", "black": "#101820", "red": "#ef6868",
                  "green": "#64cc91", "brown": "#dfc16d", "blue": "#709ef0",
                  "magenta": "#c89fee", "cyan": "#66c9d1", "white": "#dddddd"}
        image = Image.new("RGB", (self.screen.columns * 10 + 20, self.screen.lines * 23 + 20),
                          "#101820")
        draw = ImageDraw.Draw(image)
        for y, row in self.screen.buffer.items():
            for x, char in row.items():
                if char.data:
                    color = colors.get(char.fg, "#" + char.fg if len(char.fg) == 6 else "#d4d4d4")
                    draw.text((10 + x * 10, 10 + y * 23), char.data, font=font, fill=color)
        image.save(self.output / (name + ".png"))
        (self.output / (name + ".txt")).write_text("\n".join(self.screen.display), encoding="utf-8")

    def close(self):
        (self.output / "terminal.vt.txt").write_text(self.raw, encoding="utf-8")
        self.proc.close(force=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    os.environ["AGENTHUB_TEST_PYTHON"] = str(args.python.resolve())
    from test_cli_integration import cli_fixture
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    evidence = {"python": str(args.python.resolve()), "model": "deterministic HTTP fixture",
                "terminal": "Windows ConPTY", "status": "running", "checks": []}
    try:
        with tempfile.TemporaryDirectory(prefix="agenthub-terminal-") as temporary:
            with contextlib.contextmanager(cli_fixture.__wrapped__)(Path(temporary)) as fixture:
                run, project, _, requests = fixture
                run("trust", "add", str(project))
                run("--json", "-p", "REPAIR")
                terminal = Terminal([run.python, "-B", "-m", "agent_cli.main", "-r"],
                                    project, run.environment, output)
                try:
                    terminal.wait("恢复会话")
                    terminal.snapshot("01-selector")
                    mark = terminal.send("\r")
                    terminal.wait("agenthub>", after=mark)
                    terminal.snapshot("02-restored")
                    evidence["checks"].append("resume without ID and visible history")
                    mark = terminal.send("VISUAL\r")
                    terminal.wait("powershell.run", after=mark)
                    terminal.snapshot("03-running")
                    terminal.wait("value_44", after=mark)
                    terminal.wait("Run:", after=mark)
                    terminal.wait("agenthub>", after=terminal.raw.rfind("Run:"))
                    terminal.snapshot("04-completed")
                    assert "value_0" in terminal.raw and "value_44" in terminal.raw
                    assert "value_44" in "\n".join(terminal.screen.display)
                    scrollback = "\n".join("".join(c.data for _, c in sorted(row.items()))
                                           for row in terminal.screen.history.top)
                    assert "value_0" in scrollback
                    evidence["checks"].append("long Markdown stream retained in scrollback")
                    terminal.proc.setwinsize(22, 60)
                    terminal.screen.resize(lines=22, columns=60)
                    mark = terminal.send("/resume\r")
                    terminal.wait("恢复会话", after=mark)
                    terminal.snapshot("05-narrow-selector")
                    terminal.send("\x1b")
                    before = len(requests)
                    mark = terminal.send("/tools\r")
                    terminal.wait("选择运行", after=mark)
                    terminal.send("\r")
                    terminal.wait("选择工具调用", after=mark)
                    terminal.snapshot("06-tool-selector")
                    terminal.send("\x1b")
                    assert len(requests) == before
                    evidence["checks"].append("tools browsing is read only")
                    mark = terminal.send("SLOW_visual\r")
                    deadline = time.monotonic() + 30
                    while not (project / "tree-pids.txt").exists():
                        terminal.pump()
                        if time.monotonic() > deadline:
                            raise AssertionError("process fixture did not start")
                    import win32api
                    import win32event
                    handles = [win32api.OpenProcess(0x00100000, False, int(pid))
                               for pid in (project / "tree-pids.txt").read_text().split()]
                    try:
                        terminal.send("\x03")
                        terminal.wait("cancelled", after=mark)
                        assert all(win32event.WaitForSingleObject(h, 5000) == 0 for h in handles)
                    finally:
                        for handle in handles:
                            handle.Close()
                    terminal.wait("agenthub>", after=mark + 20)
                    terminal.snapshot("07-cancelled")
                    evidence["checks"].append("Ctrl+C returns to usable prompt")
                    terminal.send("/exit\r")
                    deadline = time.monotonic() + 15
                    while terminal.proc.isalive() and time.monotonic() < deadline:
                        terminal.pump()
                    assert not terminal.proc.isalive()
                    evidence["exit_code"] = terminal.proc.exitstatus
                    assert evidence["exit_code"] == 0
                    evidence["checks"].append("normal exit and terminal restoration")
                finally:
                    terminal.close()
        evidence["status"] = "passed"
    except BaseException as exc:
        evidence["status"], evidence["error"] = "failed", str(exc)
        raise
    finally:
        (output / "acceptance.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2),
                                                 encoding="utf-8")


if __name__ == "__main__":
    main()
