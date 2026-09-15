"""Errors owned by shared contracts rather than a provider SDK."""


class OutputTokenLimitExceeded(RuntimeError):
    """A model response exceeded the available output or run token budget."""

    usage: dict | None = None


class ConfigurationError(RuntimeError):
    """Invalid configuration or an unavailable required capability."""


class ModelInvocationError(RuntimeError):
    """The selected provider could not complete a model request."""

    usage: dict | None = None


class OperationError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)
