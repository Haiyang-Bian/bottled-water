from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.models import Conversation
from app.services.workspaces.filesystem import (
    resolve_workspace_path,
    safe_segment,
    scoped_dir,
    workspace_id_from_args,
    workspace_root,
)
from app.services.tools.execution_root import trusted_execution_root


def external_agent_cwd(
    db: Session,
    arguments: dict[str, Any],
    *,
    provider: str,
) -> tuple[str, Path]:
    workspace_id = workspace_id_from_args(db, arguments)
    conversation_id = str(arguments.get("conversation_id") or "") or None
    agent_id = str(arguments.get("agent_id") or "") or None
    requested = str(arguments.get("cwd") or arguments.get("workdir") or "").strip()
    execution_root = trusted_execution_root(arguments)

    if requested:
        root = execution_root or workspace_root(workspace_id)
        cwd = resolve_workspace_path(root, requested, allow_empty=True)
    elif execution_root is not None:
        cwd = execution_root
    else:
        cwd = scoped_dir(
            workspace_id,
            "tools",
            conversation_id=conversation_id,
            agent_id=agent_id,
        ) / safe_segment(provider)
        cwd.mkdir(parents=True, exist_ok=True)

    root = (execution_root or workspace_root(workspace_id)).resolve()
    resolved = cwd.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValidationAppError("external agent cwd escapes workspace")
    return workspace_id, resolved


def conversation_workspace_id(db: Session, conversation_id: str | None) -> str | None:
    if not conversation_id:
        return None
    conversation = db.get(Conversation, conversation_id)
    if not conversation or not isinstance(conversation.extra, dict):
        return None
    value = conversation.extra.get("workspace_id") or conversation.extra.get("workspaceId")
    return str(value) if value else None


def snapshot_files(root: Path) -> dict[str, tuple[int, float, str]]:
    if not root.exists():
        return {}
    snapshot: dict[str, tuple[int, float, str]] = {}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            digest = hashlib.sha1(path.read_bytes()).hexdigest()[:16]
            snapshot[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime, digest)
        except OSError:
            continue
    return snapshot


def changed_files(root: Path, before: dict[str, tuple[int, float, str]]) -> list[dict[str, Any]]:
    after = snapshot_files(root)
    changed: list[dict[str, Any]] = []
    for rel_path, state in after.items():
        old = before.get(rel_path)
        if old == state:
            continue
        changed.append(
            {
                "path": rel_path,
                "size": state[0],
                "updated_at": state[1],
                "change": "created" if old is None else "modified",
            }
        )
    for rel_path in sorted(set(before) - set(after)):
        changed.append({"path": rel_path, "change": "deleted"})
    return changed[:200]
