"""Project Runtime Kernel envelopes onto the current AgentHub UI protocol."""

from __future__ import annotations

from agent_runtime.core.run_types import EventEnvelope
from agent_runtime.core.types import Event


def project_runtime_event(envelope: EventEnvelope, *, replayed: bool = False) -> Event:
    payload = dict(envelope.payload)
    payload.setdefault("generation_id", envelope.run_id)
    payload.setdefault("conversation_id", envelope.context_scope_id)
    payload["runtime_event_id"] = envelope.event_id
    payload["runtime_sequence"] = envelope.sequence
    payload["runtime_run_id"] = envelope.run_id
    payload["runtime_replayed"] = replayed
    event_type = envelope.type
    if event_type == "system.run_started":
        event_type = "system.session_started"
        payload.setdefault("session_id", envelope.context_scope_id)
    elif event_type == "system.run_completed":
        event_type = "system.session_completed"
        payload.setdefault("session_id", envelope.context_scope_id)
    elif event_type == "system.run_cancelled":
        event_type = "system.session_cancelled"
        payload.setdefault("session_id", envelope.context_scope_id)
    elif event_type == "scheduler.proposal":
        event_type = "scheduler.decision"
        payload = {
            **payload,
            "decision": {
                "decision_type": payload.get("action"),
                "target_agent_ids": payload.get("target_agent_ids") or [],
                "target_agent_id": next(iter(payload.get("target_agent_ids") or []), None),
                "task_description": payload.get("task") or "",
                "rationale": payload.get("rationale") or "",
            },
        }
    return Event(
        type=event_type,
        payload=payload,
        source=envelope.source,
        target=envelope.target,
        correlation_id=envelope.correlation_id,
        timestamp=envelope.occurred_at,
    )
