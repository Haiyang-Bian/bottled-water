"""Render external text literally; terminal escape sequences are never commands."""

import re

_ESCAPES = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\|$)|\x1b\[[0-?]*[ -/]*[@-~]|\x1b.")


def safe_text(value):
    value = _ESCAPES.sub("", str(value))
    return "".join(c for c in value if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) < 160))
