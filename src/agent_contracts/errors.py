"""Errors owned by shared contracts rather than a provider SDK."""


class OutputTokenLimitExceeded(RuntimeError):
    """A model response exceeded the available output or run token budget."""


class ConfigurationError(RuntimeError):
    """Invalid configuration or an unavailable required capability."""


class OperationError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
