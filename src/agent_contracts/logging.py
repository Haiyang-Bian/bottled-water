"""Dependency-free structured logging. Only hosts configure handlers."""

import logging


class Logger:
    def __init__(self, name: str):
        self.logger = logging.getLogger(name)

    def _write(self, level: int, message: str, **fields):
        exc_info = fields.pop("exc_info", False)
        self.logger.log(
            level,
            message,
            extra={"context": " ".join(f"{key}={value}" for key, value in fields.items())},
            exc_info=exc_info,
        )

    def debug(self, message: str, **fields):
        self._write(logging.DEBUG, message, **fields)

    def info(self, message: str, **fields):
        self._write(logging.INFO, message, **fields)

    def warning(self, message: str, **fields):
        self._write(logging.WARNING, message, **fields)

    def error(self, message: str, **fields):
        self._write(logging.ERROR, message, **fields)

    def exception(self, message: str, **fields):
        self._write(logging.ERROR, message, exc_info=True, **fields)


def get_logger(name: str) -> Logger:
    return Logger(name)
