"""Secret redaction shared by local storage, tools and presentation."""

import json
from dataclasses import asdict, is_dataclass
from datetime import datetime
from enum import Enum


def json_default(value):
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, (datetime, Enum)):
        return value.isoformat() if isinstance(value, datetime) else value.value
    raise TypeError(f"Unsupported record type: {type(value).__name__}")


class Redactor:
    def __init__(self, secrets=()):
        self.secrets = tuple(sorted({s for s in secrets if s}, key=len, reverse=True))

    def text(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[redacted]")
        return text

    def value(self, value):
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, dict):
            return {k: self.value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.value(v) for v in value]
        return value

    def dumps(self, value):
        # Redact before JSON escaping so quoted/unicode secrets remain detectable.
        normalized = json.loads(json.dumps(value, default=json_default, ensure_ascii=False))
        return json.dumps(self.value(normalized), ensure_ascii=False, separators=(",", ":"))


class RedactedStream:
    """Hold possible secret prefixes across arbitrary streaming chunk boundaries."""

    def __init__(self, redactor):
        self.redactor = redactor
        self.pending = ""

    def push(self, text, final=False):
        self.pending += text
        self.pending = self.redactor.text(self.pending)
        keep = 0
        if not final:
            for secret in self.redactor.secrets:
                for size in range(1, min(len(secret), len(self.pending) + 1)):
                    if self.pending.endswith(secret[:size]):
                        keep = max(keep, size)
        result = self.pending[:-keep] if keep else self.pending
        self.pending = self.pending[-keep:] if keep else ""
        return result
