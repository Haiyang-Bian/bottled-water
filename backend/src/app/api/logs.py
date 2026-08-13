from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import APIRouter, Depends

from app.core.logging_config import get_frontend_logger
from app.core.response import ok
from app.deps import get_current_user
from app.schemas.common import FrontendLogBatch
from app.services.serialization import redact_sensitive

router = APIRouter(tags=["logs"])

_level_map: dict[str, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
}

_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE)
_PROVIDER_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE)


def _redact_frontend_log_value(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = redact_sensitive(value)
        return {
            key: _redact_frontend_log_value(item)
            for key, item in redacted.items()
        }
    if isinstance(value, list):
        return [_redact_frontend_log_value(item) for item in value]
    if not isinstance(value, str):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return _PROVIDER_KEY.sub(
            "<redacted>",
            _BEARER_TOKEN.sub("Bearer <redacted>", value),
        )
    return json.dumps(redact_sensitive(parsed), ensure_ascii=False)


@router.post("/logs")
async def receive_frontend_logs(
    batch: FrontendLogBatch,
    _user=Depends(get_current_user),
):
    """接收前端批量日志，写入 frontend-YYYY-MM-DD.log。"""
    logger = get_frontend_logger()
    for entry in batch.logs:
        level = _level_map.get(entry.level.upper(), logging.INFO)
        extra_parts: list[str] = []
        if entry.url:
            extra_parts.append(f"url={_redact_frontend_log_value(entry.url)}")
        if entry.data:
            extra_parts.append(f"data={_redact_frontend_log_value(entry.data)}")
        extra = f" | {' | '.join(extra_parts)}" if extra_parts else ""
        logger.log(level, "[%s] %s%s", entry.module, entry.message, extra)
    return ok()
