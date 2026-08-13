from __future__ import annotations

import uuid
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.core.protocol import (
    AGENT_FAILED,
    AGENT_REPORT,
    AGENT_STATE_CHANGED,
    BLACKBOARD_UPDATED,
    SCHEDULER_DECISION,
    SCHEDULER_PLAN,
    SCHEDULER_SUMMARY,
)
from agent_runtime.core.types import Event
from db.models import Conversation, utcnow

MAX_GENERATION_HISTORY = 20
MAX_DECISION_HISTORY = 20
MAX_RUNTIME_SUMMARIES = 20
TERMINAL_GENERATION_STATUSES = {"completed", "failed", "cancelled", "canceled"}


@dataclass(frozen=True)
class RecoveredGeneration:
    conversation_id: str
    generation_id: str
    status: str
    error: str | None = None


def _now_iso() -> str:
    return utcnow().isoformat()


def _agent_snapshot(agent: Any) -> dict[str, Any]:
    if isinstance(agent, dict):
        return {
            "agent_id": str(agent.get("id") or agent.get("agent_id") or ""),
            "agent_name": str(agent.get("name") or agent.get("agent_name") or "Agent"),
            "role": str(agent.get("role") or agent.get("type") or "worker"),
            "status": "queued",
            "started_at": None,
            "completed_at": None,
            "error": None,
            "output_preview": "",
        }
    return {
        "agent_id": str(getattr(agent, "id", "")),
        "agent_name": str(getattr(agent, "name", "Agent")),
        "role": str(getattr(agent, "role", getattr(agent, "type", "worker")) or "worker"),
        "status": "queued",
        "started_at": None,
        "completed_at": None,
        "error": None,
        "output_preview": "",
    }


def _runtime_payload(conversation: Conversation) -> tuple[dict[str, Any], dict[str, Any]]:
    extra = dict(conversation.extra or {})
    runtime = dict(extra.get("runtime") or {})
    return extra, runtime


def _generation_index(runtime: dict[str, Any], generation_id: str) -> int | None:
    for index, item in enumerate(runtime.get("generations") or []):
        if str(item.get("id")) == generation_id:
            return index
    return None


def _set_runtime(conversation: Conversation, runtime: dict[str, Any]) -> None:
    extra = dict(conversation.extra or {})
    extra["runtime"] = deepcopy(runtime)
    conversation.extra = extra


async def create_generation_record(
    db: AsyncSession,
    conversation_id: str,
    *,
    run_id: str | None = None,
    agents: Iterable[Any],
    prompt: str,
    user_message_id: str | None = None,
    model_config_id: str | None = None,
    scheduling_strategy: str | None = None,
    workflow_enabled: bool | None = None,
) -> str:
    """创建可恢复的 generation 运行记录。"""
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        raise ValueError(f"Conversation not found: {conversation_id}")

    generation_id = run_id or str(uuid.uuid4())
    _extra, runtime = _runtime_payload(conversation)
    generations = list(runtime.get("generations") or [])
    record = {
        "id": generation_id,
        "run_id": generation_id,
        "status": "running",
        "model_config_id": model_config_id,
        "user_message_id": user_message_id,
        "scheduling_strategy": scheduling_strategy,
        "workflow_enabled": bool(workflow_enabled),
        "prompt_preview": prompt[:300],
        "started_at": _now_iso(),
        "completed_at": None,
        "cancelled_at": None,
        "error": None,
        "event_counts": {},
        "task_plan": [],
        "summary": {},
        "summaries": [],
        "decisions": [],
        "watchdog_events": [],
        "agent_runs": [_agent_snapshot(agent) for agent in agents],
    }
    generations.append(record)
    runtime["generations"] = generations[-MAX_GENERATION_HISTORY:]
    runtime["active_generation_id"] = generation_id
    _set_runtime(conversation, runtime)
    conversation.generation_status = "running"
    conversation.active_run_id = generation_id
    await db.commit()
    return generation_id


async def record_generation_event(
    db: AsyncSession,
    conversation_id: str,
    generation_id: str,
    event: Event,
) -> None:
    """把关键 runtime 事件折叠进当前 generation 记录。"""
    if not _should_record_event(event):
        return
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        return
    _extra, runtime = _runtime_payload(conversation)
    index = _generation_index(runtime, generation_id)
    if index is None:
        return

    generations = list(runtime.get("generations") or [])
    record = dict(generations[index])
    event_counts = dict(record.get("event_counts") or {})
    event_counts[event.type] = int(event_counts.get(event.type) or 0) + 1
    record["event_counts"] = event_counts
    _record_plan_event(record, event)
    _record_agent_event(record, event)
    _record_decision_event(record, event)
    _record_summary_event(record, event)
    _record_watchdog_event(record, event)
    generations[index] = record
    runtime["generations"] = generations
    _set_runtime(conversation, runtime)
    await db.commit()


async def finish_generation_record(
    db: AsyncSession,
    conversation_id: str,
    generation_id: str,
    *,
    status: str,
    error: str | None = None,
) -> None:
    """把 generation 收敛到 completed / failed / cancelled。"""
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        return
    _extra, runtime = _runtime_payload(conversation)
    index = _generation_index(runtime, generation_id)
    if index is None:
        return

    generations = list(runtime.get("generations") or [])
    record = dict(generations[index])
    terminal_status = _normalize_terminal_status(status)
    record["status"] = terminal_status
    record["completed_at"] = record.get("completed_at") or _now_iso()
    if terminal_status == "cancelled":
        record["cancelled_at"] = record.get("cancelled_at") or record["completed_at"]
    if error:
        record["error"] = error[:1000]
    _mark_open_agents_for_terminal_status(record, terminal_status, error)
    generations[index] = record
    runtime["generations"] = generations
    if runtime.get("active_generation_id") == generation_id:
        runtime["active_generation_id"] = None
    _set_runtime(conversation, runtime)
    conversation.generation_status = "idle" if terminal_status == "completed" else terminal_status
    if conversation.active_run_id == generation_id:
        conversation.active_run_id = None
    await db.commit()


async def fail_abandoned_generation_record(
    db: AsyncSession,
    conversation_id: str,
    *,
    reason: str = "process_lost",
) -> str | None:
    """Cancel a persisted running generation that no in-process task owns."""
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        return None
    recovered = _fail_abandoned_generation(conversation, reason=reason)
    if recovered:
        await db.commit()
        return recovered.generation_id
    return None


async def cancel_abandoned_generation_record(
    db: AsyncSession,
    conversation_id: str,
    *,
    reason: str = "user_cancelled",
) -> str | None:
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        return None
    recovered = _fail_abandoned_generation(
        conversation, reason=reason, terminal_status="cancelled"
    )
    if recovered:
        await db.commit()
        return recovered.generation_id
    return None


async def recover_abandoned_generation_record(
    db: AsyncSession,
    conversation_id: str,
    *,
    reason: str = "process_lost",
) -> RecoveredGeneration | None:
    """Recover a persisted active generation that no in-process task owns."""
    conversation = await db.get(Conversation, conversation_id)
    if not conversation:
        return None
    recovered = _fail_abandoned_generation(conversation, reason=reason)
    if recovered:
        await db.commit()
    return recovered


async def fail_abandoned_generation_records(
    db: AsyncSession,
    *,
    reason: str = "process_lost",
) -> list[RecoveredGeneration]:
    """Cancel persisted running generations left behind by a process restart."""
    result = await db.scalars(
        select(Conversation).where(Conversation.deleted_at.is_(None))
    )
    recovered: list[RecoveredGeneration] = []
    for conversation in result.all():
        recovered_generation = _fail_abandoned_generation(conversation, reason=reason)
        if recovered_generation:
            recovered.append(recovered_generation)
    if recovered:
        await db.commit()
    return recovered


def _fail_abandoned_generation(
    conversation: Conversation,
    *,
    reason: str,
    terminal_status: str = "failed",
) -> RecoveredGeneration | None:
    _extra, runtime = _runtime_payload(conversation)
    generations = list(runtime.get("generations") or [])
    active_generation_id = str(runtime.get("active_generation_id") or "")
    candidate_ids: list[str] = []
    if active_generation_id:
        candidate_ids.append(active_generation_id)

    conversation_status = str(conversation.generation_status or "").lower()
    if conversation_status in {"running", "executing"} and generations:
        latest_id = str(generations[-1].get("id") or "")
        if latest_id and latest_id not in candidate_ids:
            candidate_ids.append(latest_id)

    if not candidate_ids and conversation_status not in {"running", "executing"}:
        return None

    completed_at = _now_iso()
    recovered_generation_id: str | None = None
    terminal_candidate_status: str | None = None
    updated_generations: list[dict[str, Any]] = []
    for item in generations:
        record = dict(item)
        record_id = str(record.get("id") or "")
        status = str(record.get("status") or "").lower()
        if record_id in candidate_ids and status not in TERMINAL_GENERATION_STATUSES:
            record["status"] = terminal_status
            record["completed_at"] = record.get("completed_at") or completed_at
            if terminal_status == "cancelled":
                record["cancelled_at"] = record.get("cancelled_at") or record["completed_at"]
            record["error"] = str(reason or "process_lost")[:1000]
            _mark_open_agents_for_terminal_status(record, terminal_status, record["error"])
            recovered_generation_id = record_id
        elif record_id in candidate_ids and status in TERMINAL_GENERATION_STATUSES:
            terminal_candidate_status = "cancelled" if status == "canceled" else status
        updated_generations.append(record)

    changed = recovered_generation_id is not None
    if active_generation_id in candidate_ids:
        runtime["active_generation_id"] = None
        changed = True

    if not changed and conversation_status not in {"running", "executing"}:
        return None

    runtime["generations"] = updated_generations
    _set_runtime(conversation, runtime)
    if recovered_generation_id or not terminal_candidate_status:
        recovered_status = terminal_status
        conversation_status = terminal_status
        error = str(reason or "process_lost")[:1000]
    else:
        recovered_status = terminal_candidate_status
        conversation_status = "idle" if terminal_candidate_status == "completed" else terminal_candidate_status
        error = None
    conversation.generation_status = conversation_status
    conversation.active_run_id = None
    generation_id = recovered_generation_id or (candidate_ids[0] if candidate_ids else "status-only")
    return RecoveredGeneration(
        conversation_id=str(conversation.id),
        generation_id=generation_id,
        status=recovered_status,
        error=error,
    )


def _should_record_event(event: Event) -> bool:
    if event.type.startswith("agent.token"):
        return False
    return event.type.startswith(("system.", "control.", "agent.", "user.", "scheduler.")) or event.type == BLACKBOARD_UPDATED


def _record_agent_event(record: dict[str, Any], event: Event) -> None:
    payload = event.payload or {}
    report_payload = payload.get("report") if isinstance(payload.get("report"), dict) else {}
    agent_id = str(payload.get("agent_id") or report_payload.get("agent_id") or "")
    if not agent_id:
        return
    agent_runs = list(record.get("agent_runs") or [])
    for item in agent_runs:
        if str(item.get("agent_id")) == agent_id:
            _apply_agent_status(item, event)
            break
    else:
        item = _agent_snapshot(
            {
                "id": agent_id,
                "name": payload.get("agent_name") or payload.get("name") or "Agent",
                "role": payload.get("role") or "worker",
            }
        )
        _apply_agent_status(item, event)
        agent_runs.append(item)
    record["agent_runs"] = agent_runs


def _record_plan_event(record: dict[str, Any], event: Event) -> None:
    if event.type not in {SCHEDULER_PLAN, SCHEDULER_DECISION}:
        return
    payload = event.payload or {}
    plan = payload.get("plan")
    if isinstance(plan, list):
        record["task_plan"] = deepcopy(plan)


def _apply_agent_status(item: dict[str, Any], event: Event) -> None:
    payload = event.payload or {}
    if event.type == AGENT_STATE_CHANGED:
        state = _normalize_agent_run_status(payload.get("state"))
        item["status"] = state
        if state == "running":
            item["started_at"] = item.get("started_at") or _now_iso()
            item["error"] = None
        if state in {"completed", "failed", "cancelled"}:
            item["completed_at"] = item.get("completed_at") or _now_iso()
        if state == "failed":
            item["error"] = str(payload.get("reason") or "Agent failed")[:1000]
        item["last_reason"] = str(payload.get("reason") or "")[:300]
        item["current_task"] = str(payload.get("task") or item.get("current_task") or "")[:300]
        return
    if event.type == AGENT_REPORT:
        report = payload.get("report") if isinstance(payload.get("report"), dict) else {}
        state = _normalize_agent_run_status(report.get("state"))
        item["status"] = state
        item["completed_at"] = item.get("completed_at") or _now_iso()
        if payload.get("input") is not None:
            item["input"] = deepcopy(payload.get("input"))
        if payload.get("output") is not None:
            item["output"] = deepcopy(payload.get("output"))
        tool_events = payload.get("tool_events")
        if isinstance(tool_events, list):
            item["tool_events"] = deepcopy(tool_events[-20:])
            item["tool_count"] = sum(
                len(event.get("results") or [])
                for event in tool_events
                if isinstance(event, dict)
            )
        item["output_preview"] = str(payload.get("work_product") or "")[:300]
        item["rationale"] = str(report.get("rationale") or "")[:500]
        item["will"] = str(report.get("will") or "")[:50]
        item["confidence"] = report.get("confidence")
        if state == "failed":
            item["error"] = "; ".join(str(x) for x in report.get("blockers") or [])[:1000]
        else:
            item["error"] = None
        return
    if event.type == AGENT_FAILED:
        item["status"] = "failed"
        item["completed_at"] = item.get("completed_at") or _now_iso()
        item["error"] = str(payload.get("error") or "Agent failed")[:1000]
        return
    if event.type.endswith("agent_started"):
        item["status"] = "running"
        item["started_at"] = item.get("started_at") or _now_iso()
        item["error"] = None
        return
    if event.type.endswith("agent_completed"):
        item["status"] = "completed"
        item["completed_at"] = _now_iso()
        item["output_preview"] = str(payload.get("work_product") or "")[:300]
        item["error"] = None
        return
    if event.type.endswith("agent_failed"):
        item["status"] = "failed"
        item["completed_at"] = _now_iso()
        item["error"] = str(payload.get("error") or "Agent failed")[:1000]


def _record_decision_event(record: dict[str, Any], event: Event) -> None:
    if event.type not in {"control.scheduling_decision", SCHEDULER_DECISION}:
        return
    payload = event.payload or {}
    decision_payload = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    decision_type = (
        decision_payload.get("action")
        or decision_payload.get("decision_type")
        or decision_payload.get("decision")
        or payload.get("decision")
    )
    targets = decision_payload.get("target_agent_ids") or payload.get("target_agent_ids") or []
    target = decision_payload.get("target_agent_id") or payload.get("target") or (
        targets[0] if isinstance(targets, list) and targets else None
    )
    task = decision_payload.get("task") or decision_payload.get("task_description") or payload.get("task")
    rationale = decision_payload.get("rationale") or payload.get("rationale")
    decisions = list(record.get("decisions") or [])
    decisions.append(
        {
            "round": payload.get("round"),
            "decision": decision_type,
            "target": target,
            "target_agent_ids": targets if isinstance(targets, list) else [],
            "task": str(task or "")[:300],
            "rationale": str(rationale or "")[:500],
            "expected_outputs": decision_payload.get("expected_outputs") or [],
            "requires_review": bool(
                decision_payload.get("requires_review")
                or decision_payload.get("requires_verification")
            ),
            "fallback_reason": decision_payload.get("fallback_reason"),
            "summary": deepcopy(payload.get("summary")) if isinstance(payload.get("summary"), dict) else {},
            "raw": deepcopy(payload),
            "created_at": _now_iso(),
        }
    )
    record["decisions"] = decisions[-MAX_DECISION_HISTORY:]


def _record_summary_event(record: dict[str, Any], event: Event) -> None:
    if event.type not in {SCHEDULER_SUMMARY, SCHEDULER_DECISION} and event.type != "control.complete":
        return
    payload = event.payload or {}
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else None
    if summary is None and event.type == SCHEDULER_SUMMARY:
        summary = payload
    if not isinstance(summary, dict):
        return
    record["summary"] = deepcopy(summary)
    summaries = list(record.get("summaries") or [])
    summaries.append({"created_at": _now_iso(), **deepcopy(summary)})
    record["summaries"] = summaries[-MAX_RUNTIME_SUMMARIES:]
    plan = summary.get("plan")
    if isinstance(plan, list):
        record["task_plan"] = deepcopy(plan)


def _record_watchdog_event(record: dict[str, Any], event: Event) -> None:
    if event.type != "control.watchdog_triggered":
        return
    events = list(record.get("watchdog_events") or [])
    events.append({"payload": deepcopy(event.payload), "created_at": _now_iso()})
    record["watchdog_events"] = events[-MAX_DECISION_HISTORY:]


def _mark_open_agents_for_terminal_status(
    record: dict[str, Any],
    status: str,
    error: str | None,
) -> None:
    updated = []
    for item in record.get("agent_runs") or []:
        agent_run = dict(item)
        current = str(agent_run.get("status") or "").lower()
        is_open = current in {"running", "waiting", "paused"}
        is_pending = current in {"queued", "ready", "idle"}
        if status == "completed" and is_open:
            agent_run["status"] = "completed"
            agent_run["completed_at"] = agent_run.get("completed_at") or _now_iso()
        elif status in {"failed", "cancelled"} and (is_open or is_pending):
            agent_run["status"] = status
            agent_run["completed_at"] = agent_run.get("completed_at") or _now_iso()
            if error:
                agent_run["error"] = error[:1000]
        updated.append(agent_run)
    record["agent_runs"] = updated


def _normalize_terminal_status(status: str) -> str:
    value = str(status or "").lower()
    if value in {"cancelled", "canceled"}:
        return "cancelled"
    if value in {"failed", "error"}:
        return "failed"
    return "completed"


def _normalize_agent_run_status(value: Any) -> str:
    status = str(value or "").lower()
    if status in {"ready", "idle", "queued"}:
        return "queued"
    if status in {"running", "working"}:
        return "running"
    if status in {"paused", "waiting"}:
        return status
    if status in {"completed", "complete", "succeeded", "success"}:
        return "completed"
    if status in {"cancelled", "canceled"}:
        return "cancelled"
    if status in {"failed", "failure", "error", "blocked"}:
        return "failed"
    return "unknown"
