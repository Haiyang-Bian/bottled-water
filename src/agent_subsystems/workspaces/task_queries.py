"""Deterministic local-date and lexical task lookup; no model-assisted classification."""

import re
from datetime import datetime, time, timedelta

from agent_contracts.errors import OperationError
from agent_subsystems.memory.rules import terms


def query_parts(query, *, since=None, until=None, now=None):
    if len(query) > 2000:
        raise OperationError("task_query_limit", "Query exceeds 2000 characters")
    now = now or datetime.now().astimezone()
    today = now.date()
    start, end = None, None
    match = re.search(r"最近\s*(\d+)\s*天|今天|昨天|前天", query)
    if match:
        token = match.group(0)
        if match.group(1):
            days = int(match.group(1))
            if not 1 <= days <= 3650:
                raise OperationError("task_date_range", "Recent days must be 1–3650")
            start, end = today - timedelta(days=days - 1), today + timedelta(days=1)
        else:
            start = today - timedelta(days={"今天": 0, "昨天": 1, "前天": 2}[token])
            end = start + timedelta(days=1)
        query = query[: match.start()] + query[match.end() :]
    try:
        if since:
            start = datetime.fromisoformat(since).date()
        if until:
            end = datetime.fromisoformat(until).date() + timedelta(days=1)
    except ValueError as exc:
        raise OperationError("task_date_range", "Use ISO dates YYYY-MM-DD") from exc
    if start and end and start >= end:
        raise OperationError("task_date_range", "Start must not be after end")
    query = re.sub(r"^(继续|恢复)\s*", "", query.strip()).strip(" 的")
    return query, (
        datetime.combine(start, time.min, now.tzinfo) if start else None,
        datetime.combine(end, time.min, now.tzinfo) if end else None,
    )


def matches_date(value, bounds):
    start, end = bounds
    if not start and not end:
        return True
    try:
        timestamp = datetime.fromisoformat(value)
        if timestamp.tzinfo is None:
            timestamp = timestamp.astimezone()
        return (not start or timestamp >= start) and (not end or timestamp < end)
    except (ValueError, TypeError):
        return False


def text_score(text, query):
    wanted = terms(query) - {"的", "了", "请"}
    if not wanted:
        return 0
    return 100 * int(query.casefold() in text.casefold()) + len(wanted & terms(text))
