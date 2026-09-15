"""Deterministic validation, lexical retrieval and bounded memory presentation."""

import hashlib
import json
import os
import re
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from agent_contracts.errors import OperationError
from agent_contracts.memory import MemoryContextItem, MemoryRevision, MemorySelection


def dumps(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value):
    return hashlib.sha256(dumps(value).encode("utf-8")).hexdigest()


def terms(text):
    text = unicodedata.normalize("NFKC", text).casefold()
    result = set(re.findall(r"[a-z0-9_]+", text))
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        result.update(segment)
        result.update(segment[i : i + 2] for i in range(len(segment) - 1))
    return result


def validate(content):
    if not isinstance(content, MemoryRevision):
        raise OperationError("invalid_memory", "Invalid memory content")
    if not 1 <= len(content.title.strip()) <= 160 or not 1 <= len(content.body.strip()) <= 2000:
        raise OperationError(
            "memory_size_limit", "Title: 1–160 characters; body: 1–2000 characters"
        )
    if content.kind not in {"preference", "environment", "decision", "experience"}:
        raise OperationError("invalid_memory_kind", "Unknown memory kind")
    if content.evidence not in {"user_stated", "observed", "inferred"}:
        raise OperationError("invalid_evidence", "Unknown evidence class")
    if any(
        len(items) > 20 or any(not isinstance(s, str) or len(s) > 160 for s in items)
        for items in (content.tags, content.aliases)
    ):
        raise OperationError("memory_labels_limit", "At most 20 tags/aliases of 160 characters")
    if content.directory and not Path(content.directory).is_absolute():
        raise OperationError("invalid_memory_scope", "Applicability directory must be absolute")
    return content


def applicable(directory, cwd):
    if not directory:
        return True
    if not cwd:
        return False
    parent = os.path.normcase(os.path.realpath(directory))
    child = os.path.normcase(os.path.realpath(cwd))
    try:
        return os.path.commonpath([parent, child]) == parent
    except ValueError:
        return False


def verify_claim(content, source_kind, saved_text):
    if content.evidence == "observed" and (source_kind != "tool" or content.body not in saved_text):
        raise OperationError(
            "unverified_claim",
            "Observed content must quote saved tool facts; use inferred for conclusions",
        )
    if content.evidence == "user_stated" and (
        source_kind != "request" or content.body not in saved_text
    ):
        raise OperationError(
            "unverified_claim", "User-stated content must quote a saved user request"
        )
    if content.kind == "preference" and content.evidence != "user_stated":
        raise OperationError(
            "unverified_preference", "External observations cannot become user preferences"
        )


def rank(record, query, cwd):
    content = record.content
    wanted = terms(query)
    name = " ".join([content.title, *content.aliases])
    return (
        -len(wanted & terms(name)),
        -int(bool(content.directory) and applicable(content.directory, cwd)),
        -len(wanted & terms(dumps(asdict(content)))),
        -datetime.fromisoformat(record.verified_at).timestamp() if record.verified_at else 0,
        record.id,
    )


def selection(records, query, cwd, *, basic_chars=2000, retrieval_chars=8000):
    remaining = {True: basic_chars, False: retrieval_chars}
    result = MemorySelection()
    for record in sorted(records, key=lambda r: (not r.content.basic, rank(r, query, cwd))):
        content = record.content
        normalized_query = unicodedata.normalize("NFKC", query).casefold().replace("\\", "/")
        explicit = any(
            name
            and unicodedata.normalize("NFKC", name).casefold().replace("\\", "/")
            in normalized_query
            for name in [content.title, *content.aliases, content.directory or ""]
        )
        if content.directory and not applicable(content.directory, cwd) and not explicit:
            continue
        if not content.basic and not terms(query) & terms(dumps(asdict(content))):
            if not content.directory or not applicable(content.directory, cwd):
                continue
        text = dumps(
            {
                "id": record.id,
                "revision": record.revision,
                "content": asdict(content),
                "sources": [asdict(s) for s in record.sources],
            }
        )
        if len(text) > remaining[content.basic]:
            result.diagnostics.setdefault("budget_omitted", []).append(record.id)
            continue
        remaining[content.basic] -= len(text)
        result.items.append(MemoryContextItem(record.id, record.revision, text, content.basic))
    return result
