"""Budgets for one model input, separate from cumulative Run consumption."""

from dataclasses import dataclass


@dataclass(frozen=True)
class ContextBudget:
    max_context_chars: int = 64_000
    context_window_tokens: int | None = None
    output_reserve_tokens: int = 4096

    def __post_init__(self):
        for name in ("max_context_chars", "context_window_tokens", "output_reserve_tokens"):
            value = getattr(self, name)
            if value is None and name == "context_window_tokens":
                continue
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.context_window_tokens is not None:
            if self.context_window_tokens <= self.output_reserve_tokens:
                raise ValueError("context_window_tokens must exceed the output reserve")
