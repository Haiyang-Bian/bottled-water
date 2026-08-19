from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.errors import ValidationAppError
from app.services.workspaces.filesystem import resolve_workspace_path


@dataclass(frozen=True)
class TrustedExecutionRoot:
    """In-process capability that cannot be constructed through a JSON tool request."""

    path: Path


def trusted_execution_root(arguments: dict[str, Any]) -> Path | None:
    capability = arguments.get("_trusted_execution_root")
    if not isinstance(capability, TrustedExecutionRoot):
        return None
    try:
        root = capability.path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationAppError("Trusted execution root is unavailable") from exc
    if not root.is_dir():
        raise ValidationAppError("Trusted execution root is unavailable")
    return root


def trusted_execution_path(
    arguments: dict[str, Any], relative: str, *, allow_empty: bool = False
) -> Path | None:
    root = trusted_execution_root(arguments)
    if root is None:
        return None
    return resolve_workspace_path(root, relative, allow_empty=allow_empty)
