"""Agent 状态自报告解析与容错。

V2 runtime 依赖 AgentReport 作为调度输入，因此模型输出的状态报告必须
经过统一 schema 校验和标准化，不能让脏 JSON 或同义字段直接污染调度。
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..core.types import AgentReport, AgentState, AgentWill


STATE_ALIASES = {
    "done": AgentState.COMPLETED,
    "complete": AgentState.COMPLETED,
    "completed": AgentState.COMPLETED,
    "finished": AgentState.COMPLETED,
    "success": AgentState.COMPLETED,
    "working": AgentState.RUNNING,
    "running": AgentState.RUNNING,
    "in_progress": AgentState.RUNNING,
    "ready": AgentState.READY,
    "idle": AgentState.IDLE,
    "paused": AgentState.PAUSED,
    "pause": AgentState.PAUSED,
    "waiting": AgentState.WAITING,
    "blocked": AgentState.WAITING,
    "failed": AgentState.FAILED,
    "error": AgentState.FAILED,
    "unknown": AgentState.UNKNOWN,
}

WILL_ALIASES = {
    "do": AgentWill.EXECUTE,
    "execute": AgentWill.EXECUTE,
    "work": AgentWill.EXECUTE,
    "continue": AgentWill.EXECUTE,
    "wait": AgentWill.WAIT,
    "waiting": AgentWill.WAIT,
    "delegate": AgentWill.DELEGATE,
    "handoff": AgentWill.DELEGATE,
    "complete": AgentWill.COMPLETE,
    "completed": AgentWill.COMPLETE,
    "done": AgentWill.COMPLETE,
    "finish": AgentWill.COMPLETE,
    "blocked": AgentWill.BLOCKED,
    "block": AgentWill.BLOCKED,
}


class AgentStatusReportPayload(BaseModel):
    """模型输出状态报告的标准化 payload。"""

    model_config = ConfigDict(extra="ignore", use_enum_values=False)

    state: AgentState = AgentState.UNKNOWN
    will: AgentWill = AgentWill.WAIT
    target_task: str | None = None
    blockers: list[str] = Field(default_factory=list)
    priority: int = 0
    confidence: float = 0.0
    rationale: str = ""
    expected_duration: int | None = None

    @field_validator("state", mode="before")
    @classmethod
    def normalize_state(cls, value: Any) -> AgentState:
        if isinstance(value, AgentState):
            return value
        normalized = str(value or "unknown").strip().lower().replace("-", "_")
        return STATE_ALIASES.get(normalized, AgentState.UNKNOWN)

    @field_validator("will", mode="before")
    @classmethod
    def normalize_will(cls, value: Any) -> AgentWill:
        if isinstance(value, AgentWill):
            return value
        normalized = str(value or "wait").strip().lower().replace("-", "_")
        return WILL_ALIASES.get(normalized, AgentWill.WAIT)

    @field_validator("blockers", mode="before")
    @classmethod
    def normalize_blockers(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            return [value] if value.strip() else []
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        return [str(value)]

    @field_validator("priority", mode="before")
    @classmethod
    def clamp_priority(cls, value: Any) -> int:
        try:
            priority = int(value)
        except (TypeError, ValueError):
            return 0
        return max(0, min(priority, 10))

    @field_validator("confidence", mode="before")
    @classmethod
    def clamp_confidence(cls, value: Any) -> float:
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(confidence, 1.0))

    @field_validator("expected_duration", mode="before")
    @classmethod
    def normalize_expected_duration(cls, value: Any) -> int | None:
        if value in (None, ""):
            return None
        try:
            duration = int(value)
        except (TypeError, ValueError):
            return None
        return max(0, duration)


AGENT_STATUS_REPORT_SCHEMA = AgentStatusReportPayload.model_json_schema()


def parse_agent_status_report(content: str, agent_id: str) -> AgentReport:
    """从模型回复中解析 AgentReport，失败时返回稳定 UNKNOWN 报告。"""

    for candidate in iter_status_report_candidates(content):
        try:
            data = json.loads(candidate)
            payload = AgentStatusReportPayload.model_validate(data)
            return AgentReport(
                agent_id=agent_id,
                state=payload.state,
                will=payload.will,
                target_task=payload.target_task,
                blockers=payload.blockers,
                priority=payload.priority,
                confidence=payload.confidence,
                rationale=payload.rationale,
                expected_duration=payload.expected_duration,
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            continue

    return AgentReport(
        agent_id=agent_id,
        state=AgentState.UNKNOWN,
        will=AgentWill.WAIT,
        rationale="无法解析 Agent 状态报告",
        confidence=0.0,
    )


def iter_status_report_candidates(content: str) -> list[str]:
    """按可信度顺序提取可能的状态报告 JSON。"""

    candidates: list[str] = []
    fenced_pattern = re.compile(r"```\s*(?:status_report|status|json)?\s*([\s\S]*?)\s*```", re.I)
    for match in fenced_pattern.finditer(content or ""):
        body = match.group(1).strip()
        if _looks_like_status_report(body):
            candidates.append(_remove_leading_status_label(body))

    decoder = json.JSONDecoder()
    text = content or ""
    for index, char in enumerate(text):
        if char != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and _payload_has_status_fields(payload):
            candidates.append(json.dumps(payload, ensure_ascii=False))

    seen: set[str] = set()
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in seen:
            unique.append(candidate)
            seen.add(candidate)
    return unique


def _remove_leading_status_label(body: str) -> str:
    lines = body.splitlines()
    if lines and lines[0].strip().lower() in {"status_report", "status"}:
        return "\n".join(lines[1:]).strip()
    return body


def _looks_like_status_report(body: str) -> bool:
    cleaned = _remove_leading_status_label(body).strip()
    if not cleaned.startswith("{"):
        return False
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return bool(re.search(r'"state"\s*:', cleaned) and re.search(r'"(?:will|rationale)"\s*:', cleaned))
    return isinstance(payload, dict) and _payload_has_status_fields(payload)


def _payload_has_status_fields(payload: dict[str, Any]) -> bool:
    return "state" in payload or "will" in payload or "rationale" in payload or "blockers" in payload
