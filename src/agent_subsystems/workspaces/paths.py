"""Canonical resource roots; these checks do not sandbox arbitrary scripts."""

import os
from pathlib import Path

from agent_contracts.errors import OperationError


def canonical_directory(value, *, base=None):
    path = Path(value).expanduser()
    if path.drive and not path.is_absolute():
        raise OperationError("ambiguous_path", "Drive-relative paths are not supported")
    if not path.is_absolute() and base is not None:
        path = base / path
    path = Path(os.path.normcase(str(path.resolve(strict=True))))
    if not path.is_dir():
        raise OperationError("not_directory", f"Not a directory: {path}")
    return path


def resolve_resource(workspace, location, value, *, directory=False, file_access_scope="workspace"):
    if file_access_scope not in {"workspace", "user"}:
        raise OperationError("invalid_access_scope", "Unsupported file access scope")
    candidate = Path(value).expanduser()
    if candidate.drive and not candidate.is_absolute():
        raise OperationError("ambiguous_path", "Drive-relative paths are not supported")
    if not candidate.is_absolute():
        candidate = location.cwd / candidate
    candidate = Path(os.path.normcase(str(candidate.resolve())))
    if file_access_scope == "workspace" and not any(
        candidate.is_relative_to(root) for root in workspace.roots
    ):
        raise OperationError(
            "outside_workspace", "Add this directory explicitly before using file tools"
        )
    if directory and not candidate.is_dir():
        raise OperationError("not_directory", f"Not a directory: {candidate}")
    return candidate


def effective_roots(roots, is_trusted):
    """Keep saved grants intact; unavailable or retargeted roots cannot authorize a Run."""
    active, inactive = [], []
    for value in roots:
        try:
            path = canonical_directory(value)
            if path != Path(value):
                reason = "path_changed"
            elif not is_trusted(path):
                reason = "not_trusted"
            else:
                active.append(path)
                continue
        except (OSError, OperationError, ValueError):
            reason = "unavailable"
        inactive.append({"path": str(value), "reason": reason})
    return tuple(active), inactive
