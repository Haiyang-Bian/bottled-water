"""
共用日志系统

为 model_provider、agent_runtime、app 提供统一的日志输出。
支持控制台（带颜色）和文件（按日期轮转）双输出。

使用示例：
    from common.logger import get_logger

    logger = get_logger("agent_runtime.run_kernel")
    logger.info("Run started", run_id="xxx")
    logger.debug("调度决策", decision="assign", target="agent_1")
    logger.warning("Token 接近上限", usage=8000, limit=10000)
    logger.error("Agent 执行失败", agent_id="xxx", error="timeout")
"""

import logging
import logging.handlers
import os
import sys
from datetime import datetime
from typing import Optional

# 日志格式
_CONSOLE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s %(message)s"
_FILE_FORMAT = "%(asctime)s [%(levelname)s] %(name)s %(context)s %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 颜色映射
_COLOR_MAP = {
    "DEBUG": "\033[36m",     # 青色
    "INFO": "\033[32m",      # 绿色
    "WARNING": "\033[33m",   # 黄色
    "ERROR": "\033[31m",     # 红色
    "CRITICAL": "\033[35m",  # 紫色
    "RESET": "\033[0m",
}


class _ColoredFormatter(logging.Formatter):
    """带颜色的控制台格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        # 构建上下文字符串
        context = getattr(record, "context", "")
        if context:
            context_str = f" [{context}]"
        else:
            context_str = ""

        # 获取原始消息
        msg = record.getMessage()

        # 颜色处理
        color = _COLOR_MAP.get(record.levelname, _COLOR_MAP["RESET"])
        reset = _COLOR_MAP["RESET"]

        # 格式化输出
        timestamp = self.formatTime(record, self.datefmt)
        return (
            f"{timestamp} {color}[{record.levelname}]{reset} "
            f"\033[90m{record.name}\033[0m{context_str} {msg}"
        )


class _PlainFormatter(logging.Formatter):
    """纯文本文件格式化器"""

    def format(self, record: logging.LogRecord) -> str:
        context = getattr(record, "context", "")
        record.context_str = f"[{context}] " if context else ""
        return super().format(record)


# 全局日志配置状态
_is_configured = False
_log_dir: Optional[str] = None


def configure(
    log_dir: Optional[str] = None,
    console_level: int = logging.INFO,
    file_level: int = logging.DEBUG,
    max_bytes: int = 10 * 1024 * 1024,  # 10MB
    backup_count: int = 5,
) -> None:
    """
    配置全局日志系统

    Args:
        log_dir: 日志文件存放目录，None 则只输出到控制台
        console_level: 控制台日志级别
        file_level: 文件日志级别
        max_bytes: 单个日志文件最大字节数
        backup_count: 保留的备份文件数
    """
    global _is_configured, _log_dir

    if _is_configured:
        return

    _log_dir = log_dir

    # 根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)

    # 清除现有处理器（避免重复）
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(console_level)
    console_handler.setFormatter(_ColoredFormatter(
        fmt=_CONSOLE_FORMAT,
        datefmt=_DATE_FORMAT,
    ))
    root_logger.addHandler(console_handler)

    # 文件处理器
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

        # 当前日期作为文件名
        today = datetime.now().strftime("%Y-%m-%d")
        log_file = os.path.join(log_dir, f"agenthub_{today}.log")

        file_handler = logging.handlers.RotatingFileHandler(
            filename=log_file,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        file_handler.setLevel(file_level)
        file_handler.setFormatter(_PlainFormatter(
            fmt=_FILE_FORMAT,
            datefmt=_DATE_FORMAT,
        ))
        root_logger.addHandler(file_handler)

    _is_configured = True


class _KwargsLogger:
    """包装 Logger，支持 kwargs 传参"""

    def __init__(self, logger: logging.Logger):
        self._logger = logger

    def _log(self, level: int, msg: str, **kwargs):
        # 提取标准 Logger 参数（exc_info, stack_info, extra）
        exc_info = kwargs.pop("exc_info", False)
        if kwargs:
            extra = {"context": " ".join(f"{k}={v}" for k, v in kwargs.items())}
            self._logger.log(level, msg, extra=extra, exc_info=exc_info)
        else:
            self._logger.log(level, msg, exc_info=exc_info)

    def debug(self, msg: str, **kwargs):
        self._log(logging.DEBUG, msg, **kwargs)

    def info(self, msg: str, **kwargs):
        self._log(logging.INFO, msg, **kwargs)

    def warning(self, msg: str, **kwargs):
        self._log(logging.WARNING, msg, **kwargs)

    def error(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)

    def critical(self, msg: str, **kwargs):
        self._log(logging.CRITICAL, msg, **kwargs)

    def exception(self, msg: str, **kwargs):
        self._log(logging.ERROR, msg, **kwargs)


def get_logger(name: str) -> "_KwargsLogger":
    """获取命名日志器

    Args:
        name: 日志器名称，建议格式 "模块名.组件名"
              如 "model_provider.ark"、"agent_runtime.run_kernel"
    """
    # 自动配置（如果没有手动配置过）
    if not _is_configured:
        # 默认只输出到控制台
        configure()

    return _KwargsLogger(logging.getLogger(name))


class LogContext:
    """
    日志上下文管理器

    用于在特定代码块中附加上下文信息（如 session_id、agent_id）。

    使用示例：
        with LogContext(session_id="xxx", agent_id="agent_1"):
            logger.info("开始执行")
            # 输出: [session_id=xxx agent_id=agent_1] 开始执行
    """

    _context_stack: list[dict] = []

    def __init__(self, **kwargs):
        self.context = kwargs

    def __enter__(self):
        LogContext._context_stack.append(self.context)
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        LogContext._context_stack.pop()

    @classmethod
    def current(cls) -> dict:
        """获取当前上下文"""
        if not cls._context_stack:
            return {}
        # 合并所有层级
        merged = {}
        for ctx in cls._context_stack:
            merged.update(ctx)
        return merged

    @classmethod
    def format_current(cls) -> str:
        """格式化当前上下文为字符串"""
        ctx = cls.current()
        if not ctx:
            return ""
        return " ".join(f"{k}={v}" for k, v in ctx.items())


def ctx_logger(name: str) -> logging.LoggerAdapter:
    """
    获取带上下文的日志器

    自动附加 LogContext 中的信息。

    使用示例：
        logger = ctx_logger("agent_runtime.run_kernel")
        with LogContext(run_id="xxx"):
            logger.info("Run started")
    """
    logger = get_logger(name)
    return logging.LoggerAdapter(logger, {})


# 自定义 LoggerAdapter，支持动态上下文
class _ContextualLoggerAdapter(logging.LoggerAdapter):
    """支持动态上下文的日志适配器"""

    def process(self, msg, kwargs):
        ctx = LogContext.format_current()
        if ctx:
            msg = f"[{ctx}] {msg}"
        return msg, kwargs


def get_ctx_logger(name: str) -> "_ContextualLoggerAdapter":
    """获取支持 LogContext 的日志器"""
    logger = get_logger(name)
    return _ContextualLoggerAdapter(logger, {})
