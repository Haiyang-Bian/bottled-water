"""Canonical resource roots; these checks do not sandbox arbitrary scripts."""

import os
from pathlib import Path

from agent_contracts.errors import OperationError


def canonical_directory(value):
    path = Path(os.path.normcase(str(Path(value).expanduser().resolve(strict=True))))
    if not path.is_dir():
        raise OperationError("not_directory", f"Not a directory: {path}")
    return path


def resolve_resource(workspace, value, *, directory=False):
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = workspace.root / candidate
    candidate = Path(os.path.normcase(str(candidate.resolve())))
    if not any(candidate.is_relative_to(root) for root in workspace.roots):
        raise OperationError(
            "outside_workspace", "Add this directory explicitly before using file tools"
        )
    if directory and not candidate.is_dir():
        raise OperationError("not_directory", f"Not a directory: {candidate}")
    return candidate
