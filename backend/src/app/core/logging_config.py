from __future__ import annotations

import logging
import logging.handlers
import re
import sys
from pathlib import Path


_SENSITIVE_LOG_TEXT = (
    (re.compile(r"([?&](?:token|access_token|api_key)=)[^&\s]+", re.IGNORECASE), r"\1<redacted>"),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.IGNORECASE), "Bearer <redacted>"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b", re.IGNORECASE), "<redacted>"),
)


def _redact_log_text(value: str) -> str:
    for pattern, replacement in _SENSITIVE_LOG_TEXT:
        value = pattern.sub(replacement, value)
    return value


class _SensitiveLogFilter(logging.Filter):
    """Redact credentials before any console or file handler formats a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_log_text(record.getMessage())
        record.args = ()
        context = getattr(record, "context", None)
        if isinstance(context, str):
            record.context = _redact_log_text(context)
        return True


def _make_log_dir() -> Path:
    """返回并确保日志目录存在。

    基于当前文件位置计算，不依赖 ROOT_DIR，避免路径漂移。
    """
    # logging_config.py 位于 src/app/core/，向上 3 层到达 backend/
    log_dir = Path(__file__).resolve().parents[3] / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir


def configure_logging(
    *,
    level: int = logging.INFO,
    log_dir: Path | None = None,
) -> None:
    """配置统一的后端日志系统，输出到控制台和文件。

    所有日志（含 uvicorn、FastAPI 及第三方库）统一由根日志记录器处理，
    避免控制台重复输出，同时全部落入文件。

    后端日志写入 application-YYYY-MM-DD.log，按日期轮转，保留 30 天。
    """
    log_format = "[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s %(context)s"
    formatter = logging.Formatter(
        log_format,
        datefmt="%Y-%m-%d %H:%M:%S",
        defaults={"context": ""},
    )
    sensitive_filter = _SensitiveLogFilter()

    log_dir = log_dir or _make_log_dir()
    backend_log = log_dir / "application.log"

    backend_handler = logging.handlers.TimedRotatingFileHandler(
        backend_log,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    backend_handler.suffix = "%Y-%m-%d.log"
    backend_handler.setFormatter(formatter)
    backend_handler.addFilter(sensitive_filter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(sensitive_filter)

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(backend_handler)

    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uvicorn_logger = logging.getLogger(name)
        for handler in uvicorn_logger.handlers[:]:
            uvicorn_logger.removeHandler(handler)
        uvicorn_logger.propagate = True
        uvicorn_logger.setLevel(level)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)
    # WatchFiles observes raw writes to runtime logs and SQLite sidecars even
    # when Uvicorn later filters them out. Suppress its feedback-loop noise.
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
    logging.getLogger("watchfiles.main").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("后端日志系统已初始化，文件: %s", backend_log)


_frontend_logger: logging.Logger | None = None


def get_frontend_logger() -> logging.Logger:
    """返回前端日志记录器，懒加载。

    前端日志写入 frontend-YYYY-MM-DD.log，按日期轮转，保留 30 天。
    与后端 application-YYYY-MM-DD.log 完全隔离。
    """
    global _frontend_logger
    if _frontend_logger is not None:
        return _frontend_logger

    log_format = "[%(asctime)s] [%(levelname)s] [frontend] %(message)s"
    formatter = logging.Formatter(log_format, datefmt="%Y-%m-%d %H:%M:%S")

    log_dir = _make_log_dir()
    frontend_log = log_dir / "frontend.log"

    handler = logging.handlers.TimedRotatingFileHandler(
        frontend_log,
        when="midnight",
        interval=1,
        backupCount=30,
        encoding="utf-8",
    )
    handler.suffix = "%Y-%m-%d.log"
    handler.setFormatter(formatter)

    logger = logging.getLogger("frontend")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    logger.addHandler(handler)

    _frontend_logger = logger
    return logger
