from __future__ import annotations

import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.models import SandboxSession, User, utcnow
from app.services.tools.builtins.sandbox.policy import (
    MAX_TIMEOUT_SECONDS,
    clean_output,
    validate_test_command,
)
from app.services.tools.builtins.sandbox.runner import run_command
from app.services.workspaces.filesystem import (
    database_workspace_id_from_args,
    list_files,
    resolve_workspace_path,
    scoped_dir,
    workspace_id_from_args,
)
from app.services.tools.execution_root import trusted_execution_root


def run_sandbox_command(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    command = _command(arguments)
    timeout = _timeout(arguments, default=10)
    session, cwd = _session_and_cwd(db, user, arguments)
    return _execute(db, session, command, cwd=cwd, timeout=timeout, test_mode=False)


def run_test_command(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    command = _command(arguments, default="pytest --version")
    validate_test_command(command)
    timeout = _timeout(arguments, default=30)
    session, cwd = _session_and_cwd(db, user, arguments, default_name="Test Sandbox")
    return _execute(db, session, command, cwd=cwd, timeout=timeout, test_mode=True)


def run_existing_sandbox_command(
    db: Session,
    user: User,
    session: SandboxSession,
    *,
    command: str,
    timeout: int,
    workdir: str = "",
) -> dict[str, Any]:
    _assert_sandbox_owner(user, session)
    filesystem_workspace_id = (session.extra or {}).get("filesystem_workspace_id") or session.workspace_id or "default"
    root = scoped_dir(str(filesystem_workspace_id), "sandbox", task_id=session.project_id)
    cwd = resolve_workspace_path(root, workdir, allow_empty=True)
    cwd.mkdir(parents=True, exist_ok=True)
    return _execute(db, session, command, cwd=cwd, timeout=min(timeout, MAX_TIMEOUT_SECONDS), test_mode=False)


def _session_and_cwd(
    db: Session,
    user: User,
    arguments: dict[str, Any],
    *,
    default_name: str = "Tool Sandbox",
) -> tuple[SandboxSession, Path]:
    workspace_id = workspace_id_from_args(db, arguments)
    database_workspace_id = database_workspace_id_from_args(db, arguments)
    session = _resolve_session(db, user, arguments, workspace_id, database_workspace_id, default_name)
    root = trusted_execution_root(arguments) or scoped_dir(
        workspace_id,
        "sandbox",
        conversation_id=str(arguments.get("conversation_id") or "") or None,
        agent_id=str(arguments.get("agent_id") or "") or None,
        task_id=str(arguments.get("task_id") or "") or None,
    )
    cwd = resolve_workspace_path(root, str(arguments.get("workdir") or ""), allow_empty=True)
    cwd.mkdir(parents=True, exist_ok=True)
    session.extra = {
        **(session.extra or {}),
        "filesystem_workspace_id": workspace_id,
        "conversation_id": str(arguments.get("conversation_id") or "") or None,
        "agent_id": str(arguments.get("agent_id") or "") or None,
        "task_id": str(arguments.get("task_id") or "") or None,
    }
    session.mounted_files = list_files(root)
    return session, cwd


def _resolve_session(
    db: Session,
    user: User,
    arguments: dict[str, Any],
    filesystem_workspace_id: str,
    database_workspace_id: str | None,
    default_name: str,
) -> SandboxSession:
    sandbox_id = str(arguments.get("sandbox_id") or "")
    if sandbox_id:
        session = db.get(SandboxSession, sandbox_id)
        if not session or session.deleted_at is not None:
            raise NotFoundError("sandbox not found")
        _assert_sandbox_owner(user, session)
        return session
    session = db.scalar(
        select(SandboxSession).where(
            SandboxSession.owner_id == user.id,
            SandboxSession.workspace_id == database_workspace_id,
            SandboxSession.name == default_name,
            SandboxSession.deleted_at.is_(None),
        )
    )
    if session:
        return session
    session = SandboxSession(
        owner_id=user.id,
        workspace_id=database_workspace_id,
        project_id=str(arguments.get("project_id") or "") or None,
        name=default_name,
        image=str(arguments.get("image") or "local-python-node"),
        status="ready",
        resource_limits={"cpu": "local", "memory": "local", "timeout_seconds": MAX_TIMEOUT_SECONDS},
        extra={"filesystem_workspace_id": filesystem_workspace_id},
    )
    db.add(session)
    db.flush()
    return session


def _execute(
    db: Session,
    session: SandboxSession,
    command: str,
    *,
    cwd: Path,
    timeout: int,
    test_mode: bool,
) -> dict[str, Any]:
    started = time.perf_counter()
    session.status = "running"
    session.last_command_at = utcnow()
    db.flush()
    try:
        result = run_command(command, cwd=cwd, timeout=timeout, test_mode=test_mode)
        status = "succeeded" if result["exit_code"] == 0 else "failed"
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        result = {
            "stdout": clean_output(exc.stdout),
            "stderr": clean_output(exc.stderr) or f"Timed out after {timeout}s",
            "exit_code": -1,
        }
    payload = _payload(command, status, started, cwd, result, session.id)
    session.status = "ready" if status in {"succeeded", "failed", "timeout"} else "error"
    session.command_history = [payload, *(session.command_history or [])][:50]
    session.mounted_files = list_files(cwd)
    db.flush()
    return payload


def _command(arguments: dict[str, Any], *, default: str = "") -> str:
    command = str(arguments.get("command") or default).strip()
    if not command:
        raise ValidationAppError("command cannot be empty")
    return command


def _timeout(arguments: dict[str, Any], *, default: int) -> int:
    return min(max(int(arguments.get("timeout") or arguments.get("timeout_seconds") or default), 1), MAX_TIMEOUT_SECONDS)


def _payload(
    command: str,
    status: str,
    started: float,
    cwd: Path,
    result: dict[str, Any],
    sandbox_id: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "capability_level": "real",
        "sandbox_id": sandbox_id,
        "command": command,
        "argv": shlex.split(command, posix=True),
        "cwd": str(cwd),
        "stdout": result["stdout"],
        "stderr": result["stderr"],
        "exit_code": result["exit_code"],
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "created_at": utcnow().isoformat(),
    }


def _assert_sandbox_owner(user: User, session: SandboxSession) -> None:
    if session.owner_id != user.id and user.role != "admin":
        raise ForbiddenError("no permission to access this sandbox")
