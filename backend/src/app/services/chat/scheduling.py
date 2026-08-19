"""Conversation scheduling strategy helpers.

This module keeps strategy resolution close to the chat boundary so API,
WebSocket, and runtime session creation use the same rules.
"""

from __future__ import annotations

from typing import Any

SUPPORTED_SCHEDULING_STRATEGIES = {"workflow", "tech_lead", "single_agent", "collaborative"}
DEFAULT_SCHEDULING_STRATEGY = "tech_lead"


def normalize_scheduling_strategy(value: Any) -> str:
    strategy = str(value or "").strip()
    return strategy if strategy in SUPPORTED_SCHEDULING_STRATEGIES else ""


def conversation_has_workflow(conversation: Any) -> bool:
    extra = conversation.extra if isinstance(getattr(conversation, "extra", None), dict) else {}
    workflow = extra.get("workflow")
    return isinstance(workflow, dict) and bool(workflow.get("nodes"))


def workflow_enabled(conversation: Any) -> bool:
    extra = conversation.extra if isinstance(getattr(conversation, "extra", None), dict) else {}
    return bool(extra.get("workflow_enabled"))


def resolve_scheduling_strategy(conversation: Any, requested: Any = None) -> str:
    """Resolve message/session scheduling with one canonical precedence order.

    Precedence:
    1. explicit message-level strategy
    2. persisted conversation strategy
    3. group conversations with a saved workflow default to workflow
    4. tech_lead fallback
    """

    chat_type = getattr(conversation, "chat_type", "")
    if chat_type == "single":
        return "single_agent"

    extra = conversation.extra if isinstance(getattr(conversation, "extra", None), dict) else {}
    enabled_workflow = bool(extra.get("workflow_enabled"))

    explicit = normalize_scheduling_strategy(requested)
    if explicit:
        if explicit == "single_agent" and chat_type != "single":
            return DEFAULT_SCHEDULING_STRATEGY
        if explicit == "workflow" and not enabled_workflow:
            return "tech_lead"
        return explicit

    persisted = normalize_scheduling_strategy(extra.get("scheduling_strategy"))
    if persisted:
        if persisted == "single_agent" and chat_type != "single":
            return DEFAULT_SCHEDULING_STRATEGY
        if persisted == "workflow" and not enabled_workflow:
            return "tech_lead"
        return persisted

    if chat_type == "group":
        return DEFAULT_SCHEDULING_STRATEGY
    return "single_agent"


def persist_scheduling_strategy(conversation: Any, strategy: str) -> bool:
    """Persist a valid strategy into conversation.extra.

    Returns True when the object was changed.
    """

    if getattr(conversation, "chat_type", "") == "single":
        normalized = "single_agent"
    else:
        normalized = normalize_scheduling_strategy(strategy)
        if normalized == "single_agent":
            normalized = DEFAULT_SCHEDULING_STRATEGY
    if not normalized:
        return False

    extra = dict(conversation.extra or {})
    changed = extra.get("scheduling_strategy") != normalized
    extra["scheduling_strategy"] = normalized
    if normalized == "workflow":
        changed = changed or not bool(extra.get("workflow_enabled"))
        extra["workflow_enabled"] = True
    elif normalized in {"tech_lead", "collaborative"}:
        changed = changed or bool(extra.get("workflow_enabled"))
        extra["workflow_enabled"] = False
    else:
        changed = changed or bool(extra.get("workflow_enabled"))
        extra["workflow_enabled"] = False
    extra.pop("runtime_mode", None)
    conversation.extra = extra
    return changed
