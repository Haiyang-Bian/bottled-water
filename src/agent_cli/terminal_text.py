"""Render external text literally; terminal escape sequences are never commands."""

import re

_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")


def safe_text(value):
    value = _ESCAPES.sub("", str(value))
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))


class TerminalTextStream:
    """Discard control sequences even when split across provider chunks."""

    def __init__(self, redactor):
        from agent_subsystems.observability.redaction import RedactedStream
        self.redaction = RedactedStream(redactor)
        self.state = "text"

    def push(self, text, final=False):
        output = []
        for c in self.redaction.push(text, final=final):
            if self.state == "escape":
                self.state = "csi" if c == "[" else (
                    "string" if c in "]P_X^" else "text"
                )
            elif self.state == "csi":
                if "@" <= c <= "~":
                    self.state = "text"
            elif self.state == "string":
                if c == "\x07":
                    self.state = "text"
                elif c == "\x1b":
                    self.state = "string_escape"
            elif self.state == "string_escape":
                self.state = "text" if c == "\\" else "string"
            elif c == "\x1b":
                self.state = "escape"
            elif c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160):
                output.append(c)
        return "".join(output)
