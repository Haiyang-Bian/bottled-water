from __future__ import annotations

import os
import shlex
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.models import SandboxSession, User, utcnow
from app.services.external_agents.events import redact_secrets
from app.services.tools.builtins.sandbox.policy import (
    ALLOWED_EXECUTABLES,
    DENIED_EXECUTABLES,
    base_executable,
    clean_output,
    validate_command_text,
)
from app.services.workspaces.filesystem import (
    database_workspace_id_from_args,
    list_files,
    resolve_workspace_path,
    scoped_dir,
    workspace_id_from_args,
)
from app.services.tools.execution_root import trusted_execution_root


MAX_TERMINAL_TIMEOUT_SECONDS = 900
DEFAULT_TERMINAL_TIMEOUT_SECONDS = 300
MAX_WAIT_MS = 60_000
OUTPUT_BUFFER_LIMIT = 60_000
OUTPUT_TAIL_LIMIT = 8_000
INPUT_EVENT_LIMIT = 20
TERMINAL_ALLOWED_EXECUTABLES = {*ALLOWED_EXECUTABLES, "npx", "npx.cmd"}


def invoke_terminal_tool(
    db: Session,
    user: User,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name == "terminal.start":
        return _start_terminal(db, user, arguments)
    if name == "terminal.send":
        return _send_terminal(db, user, arguments)
    if name == "terminal.wait_for":
        return _wait_for_terminal(db, user, arguments)
    if name == "terminal.snapshot":
        return _snapshot_terminal(db, user, arguments)
    if name == "terminal.stop":
        return _stop_terminal(db, user, arguments)
    raise NotFoundError("terminal tool not found")


def _start_terminal(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    command = _command(arguments)
    argv = _argv(command)
    timeout_seconds = _timeout_seconds(arguments)
    session, cwd, root = _session_and_cwd(db, user, arguments)
    env = _process_env(arguments)

    terminal = TERMINAL_MANAGER.start(
        user=user,
        sandbox_id=session.id,
        command=command,
        argv=argv,
        cwd=cwd,
        root=root,
        timeout_seconds=timeout_seconds,
        env=env,
    )
    session.status = "running"
    session.last_command_at = utcnow()
    session.extra = {
        **(session.extra or {}),
        "terminal_session_id": terminal.id,
        "terminal_transport": terminal.transport,
        "filesystem_workspace_id": workspace_id_from_args(db, arguments),
        "conversation_id": str(arguments.get("conversation_id") or "") or None,
        "agent_id": str(arguments.get("agent_id") or "") or None,
        "task_id": str(arguments.get("task_id") or "") or None,
    }
    session.mounted_files = list_files(root)
    db.flush()

    payload = terminal.snapshot()
    return {
        "status": "succeeded",
        "capability_level": "interactive",
        "session_id": terminal.id,
        "sandbox_id": session.id,
        **payload,
    }


def _send_terminal(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    terminal = _terminal_for_user(user, arguments)
    terminal.send(str(arguments.get("input") or ""))
    _refresh_sandbox(db, terminal)
    return {
        "status": "succeeded",
        "capability_level": "interactive",
        **terminal.snapshot(),
    }


def _wait_for_terminal(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    terminal = _terminal_for_user(user, arguments)
    patterns = _patterns(arguments)
    timeout_ms = _timeout_ms(arguments)
    matched, matched_pattern = terminal.wait_for(patterns, timeout_ms=timeout_ms)
    _refresh_sandbox(db, terminal)
    payload = terminal.snapshot()
    return {
        "status": "succeeded" if matched else "timeout",
        "capability_level": "interactive",
        "matched": matched,
        "matched_pattern": matched_pattern,
        "waited_ms": timeout_ms,
        **payload,
    }


def _snapshot_terminal(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    terminal = _terminal_for_user(user, arguments)
    _refresh_sandbox(db, terminal)
    return {
        "status": "succeeded",
        "capability_level": "interactive",
        **terminal.snapshot(),
    }


def _stop_terminal(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    terminal = _terminal_for_user(user, arguments)
    terminal.stop()
    _refresh_sandbox(db, terminal)
    return {
        "status": "succeeded",
        "capability_level": "interactive",
        **terminal.snapshot(),
    }


def _terminal_for_user(user: User, arguments: dict[str, Any]) -> "TerminalProcess":
    session_id = str(arguments.get("session_id") or "").strip()
    if not session_id:
        raise ValidationAppError("session_id is required")
    terminal = TERMINAL_MANAGER.get(session_id)
    if not terminal:
        raise NotFoundError("terminal session not found")
    if terminal.user_id != user.id and user.role != "admin":
        raise ForbiddenError("no permission to access this terminal session")
    return terminal


def _session_and_cwd(
    db: Session,
    user: User,
    arguments: dict[str, Any],
) -> tuple[SandboxSession, Path, Path]:
    database_workspace_id = database_workspace_id_from_args(db, arguments)
    session = _resolve_sandbox_session(db, user, arguments, database_workspace_id)
    workspace_id = _filesystem_workspace_id(db, arguments, session)
    root = trusted_execution_root(arguments) or scoped_dir(
        workspace_id,
        "sandbox",
        conversation_id=str(arguments.get("conversation_id") or "") or None,
        agent_id=str(arguments.get("agent_id") or "") or None,
        task_id=str(arguments.get("task_id") or "") or None,
    )
    cwd = resolve_workspace_path(root, str(arguments.get("workdir") or ""), allow_empty=True)
    cwd.mkdir(parents=True, exist_ok=True)
    return session, cwd, root


def _filesystem_workspace_id(
    db: Session,
    arguments: dict[str, Any],
    session: SandboxSession,
) -> str:
    if arguments.get("workspace_id") or arguments.get("workspaceId") or arguments.get("conversation_id"):
        return workspace_id_from_args(db, arguments)
    return str((session.extra or {}).get("filesystem_workspace_id") or session.workspace_id or "default")


def _resolve_sandbox_session(
    db: Session,
    user: User,
    arguments: dict[str, Any],
    database_workspace_id: str | None,
) -> SandboxSession:
    sandbox_id = str(arguments.get("sandbox_id") or "")
    if sandbox_id:
        session = db.get(SandboxSession, sandbox_id)
        if not session or session.deleted_at is not None:
            raise NotFoundError("sandbox not found")
        if session.owner_id != user.id and user.role != "admin":
            raise ForbiddenError("no permission to access this sandbox")
        return session

    session = db.scalar(
        select(SandboxSession).where(
            SandboxSession.owner_id == user.id,
            SandboxSession.workspace_id == database_workspace_id,
            SandboxSession.name == "Interactive Terminal Sandbox",
            SandboxSession.deleted_at.is_(None),
        )
    )
    if session:
        return session
    session = SandboxSession(
        owner_id=user.id,
        workspace_id=database_workspace_id,
        project_id=str(arguments.get("project_id") or "") or None,
        name="Interactive Terminal Sandbox",
        image=str(arguments.get("image") or "local-python-node"),
        status="ready",
        resource_limits={
            "cpu": "local",
            "memory": "local",
            "timeout_seconds": MAX_TERMINAL_TIMEOUT_SECONDS,
        },
    )
    db.add(session)
    db.flush()
    return session


def _refresh_sandbox(db: Session, terminal: "TerminalProcess") -> None:
    session = db.get(SandboxSession, terminal.sandbox_id)
    if not session:
        return
    payload = terminal.snapshot()
    session.status = "running" if payload["session_status"] == "running" else "ready"
    session.mounted_files = payload["files"]
    if payload["session_status"] != "running":
        history_item = {
            "status": payload["session_status"],
            "command": terminal.command,
            "argv": terminal.argv,
            "cwd": str(terminal.cwd),
            "stdout": payload["stdout_tail"],
            "stderr": payload["stderr_tail"],
            "exit_code": payload["exit_code"],
            "duration_ms": payload["duration_ms"],
            "created_at": utcnow().isoformat(),
            "terminal_session_id": terminal.id,
        }
        existing = list(session.command_history or [])
        if not existing or existing[0].get("terminal_session_id") != terminal.id:
            session.command_history = [history_item, *existing][:50]
    db.flush()


def _command(arguments: dict[str, Any]) -> str:
    command = str(arguments.get("command") or "").strip()
    if not command:
        raise ValidationAppError("command cannot be empty")
    return command


def _argv(command: str) -> list[str]:
    validate_command_text(command)
    argv = shlex.split(command, posix=True)
    if not argv:
        raise ValidationAppError("command cannot be empty")
    executable = Path(argv[0]).name.lower()
    if executable in DENIED_EXECUTABLES or executable not in TERMINAL_ALLOWED_EXECUTABLES:
        raise ValidationAppError(f"command executable is not allowed: {argv[0]}")
    if base_executable(executable) in {"cmd", "powershell", "pwsh", "bash", "sh"}:
        raise ValidationAppError("shells are not allowed")
    return argv


def _timeout_seconds(arguments: dict[str, Any]) -> int:
    raw = arguments.get("timeout") or arguments.get("timeout_seconds")
    value = int(raw or DEFAULT_TERMINAL_TIMEOUT_SECONDS)
    return min(max(value, 1), MAX_TERMINAL_TIMEOUT_SECONDS)


def _timeout_ms(arguments: dict[str, Any]) -> int:
    raw = arguments.get("timeout_ms") or arguments.get("timeout") or 5000
    return min(max(int(raw), 100), MAX_WAIT_MS)


def _patterns(arguments: dict[str, Any]) -> list[str]:
    value = arguments.get("patterns")
    if isinstance(value, str):
        patterns = [value]
    elif isinstance(value, list):
        patterns = [str(item) for item in value if str(item)]
    else:
        patterns = []
    if not patterns:
        raise ValidationAppError("patterns cannot be empty")
    if len(patterns) > 10:
        raise ValidationAppError("too many wait patterns")
    return patterns


def _process_env(arguments: dict[str, Any]) -> dict[str, str]:
    allowed_env = {}
    raw_env = arguments.get("env")
    if isinstance(raw_env, dict):
        for key, value in raw_env.items():
            name = str(key)
            if name.startswith("AGENTHUB_") or name in {"CI", "NO_COLOR", "FORCE_COLOR"}:
                allowed_env[name] = str(value)
    return {
        **os.environ,
        **allowed_env,
        "PYTHONIOENCODING": "utf-8",
        "PYTHONUNBUFFERED": "1",
        "PYTHONUTF8": "1",
        "NO_COLOR": allowed_env.get("NO_COLOR", "1"),
        "FORCE_COLOR": allowed_env.get("FORCE_COLOR", "0"),
        "COLUMNS": "120",
        "LINES": "40",
    }


class TerminalManager:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sessions: dict[str, TerminalProcess] = {}

    def start(
        self,
        *,
        user: User,
        sandbox_id: str,
        command: str,
        argv: list[str],
        cwd: Path,
        root: Path,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> "TerminalProcess":
        with self._lock:
            self._cleanup_locked()
            terminal = TerminalProcess.create(
                user_id=user.id,
                sandbox_id=sandbox_id,
                command=command,
                argv=argv,
                cwd=cwd,
                root=root,
                timeout_seconds=timeout_seconds,
                env=env,
            )
            self._sessions[terminal.id] = terminal
            return terminal

    def get(self, session_id: str) -> "TerminalProcess | None":
        with self._lock:
            self._cleanup_locked()
            return self._sessions.get(session_id)

    def _cleanup_locked(self) -> None:
        now = time.time()
        stale_ids = []
        for session_id, terminal in self._sessions.items():
            snapshot = terminal.snapshot()
            ended_at = terminal.ended_at or 0
            if snapshot["session_status"] != "running" and now - ended_at > 300:
                stale_ids.append(session_id)
        for session_id in stale_ids:
            self._sessions.pop(session_id, None)


@dataclass
class TerminalProcess:
    id: str
    user_id: str
    sandbox_id: str
    command: str
    argv: list[str]
    cwd: Path
    root: Path
    timeout_seconds: int
    process: subprocess.Popen[Any]
    transport: str
    started_at: float
    stdout: "BoundedText" = field(default_factory=lambda: BoundedText(OUTPUT_BUFFER_LIMIT))
    stderr: "BoundedText" = field(default_factory=lambda: BoundedText(OUTPUT_BUFFER_LIMIT))
    input_events: list[dict[str, Any]] = field(default_factory=list)
    ended_at: float | None = None
    status_override: str | None = None
    master_fd: int | None = None
    stdin_pipe: TextIO | None = None
    lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def create(
        cls,
        *,
        user_id: str,
        sandbox_id: str,
        command: str,
        argv: list[str],
        cwd: Path,
        root: Path,
        timeout_seconds: int,
        env: dict[str, str],
    ) -> "TerminalProcess":
        started_at = time.time()
        session_id = f"term_{uuid.uuid4().hex}"
        try:
            return cls._create_pty(
                session_id=session_id,
                user_id=user_id,
                sandbox_id=sandbox_id,
                command=command,
                argv=argv,
                cwd=cwd,
                root=root,
                timeout_seconds=timeout_seconds,
                env=env,
                started_at=started_at,
            )
        except (ImportError, OSError, AttributeError):
            return cls._create_pipes(
                session_id=session_id,
                user_id=user_id,
                sandbox_id=sandbox_id,
                command=command,
                argv=argv,
                cwd=cwd,
                root=root,
                timeout_seconds=timeout_seconds,
                env=env,
                started_at=started_at,
            )

    @classmethod
    def _create_pty(
        cls,
        *,
        session_id: str,
        user_id: str,
        sandbox_id: str,
        command: str,
        argv: list[str],
        cwd: Path,
        root: Path,
        timeout_seconds: int,
        env: dict[str, str],
        started_at: float,
    ) -> "TerminalProcess":
        if os.name == "nt":
            raise OSError("pty is not available on Windows")
        import pty

        master_fd, slave_fd = pty.openpty()
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(cwd),
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                shell=False,
                env=env,
            )
        finally:
            os.close(slave_fd)
        terminal = cls(
            id=session_id,
            user_id=user_id,
            sandbox_id=sandbox_id,
            command=command,
            argv=argv,
            cwd=cwd,
            root=root,
            timeout_seconds=timeout_seconds,
            process=process,
            transport="pty",
            started_at=started_at,
            master_fd=master_fd,
        )
        threading.Thread(target=terminal._read_pty, daemon=True).start()
        return terminal

    @classmethod
    def _create_pipes(
        cls,
        *,
        session_id: str,
        user_id: str,
        sandbox_id: str,
        command: str,
        argv: list[str],
        cwd: Path,
        root: Path,
        timeout_seconds: int,
        env: dict[str, str],
        started_at: float,
    ) -> "TerminalProcess":
        process = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            shell=False,
            env=env,
        )
        terminal = cls(
            id=session_id,
            user_id=user_id,
            sandbox_id=sandbox_id,
            command=command,
            argv=argv,
            cwd=cwd,
            root=root,
            timeout_seconds=timeout_seconds,
            process=process,
            transport="pipes",
            started_at=started_at,
            stdin_pipe=process.stdin,
        )
        if process.stdout:
            threading.Thread(
                target=terminal._read_pipe,
                args=(process.stdout, terminal.stdout),
                daemon=True,
            ).start()
        if process.stderr:
            threading.Thread(
                target=terminal._read_pipe,
                args=(process.stderr, terminal.stderr),
                daemon=True,
            ).start()
        return terminal

    def send(self, value: str) -> None:
        if not value:
            return
        with self.lock:
            self._poll_locked()
            if self._status_locked() != "running":
                raise ValidationAppError("terminal session is not running")
            if self.transport == "pty" and self.master_fd is not None:
                os.write(self.master_fd, value.encode("utf-8", errors="replace"))
            elif self.stdin_pipe:
                self.stdin_pipe.write(value)
                self.stdin_pipe.flush()
            else:
                raise ValidationAppError("terminal stdin is not available")
            self.input_events.append({
                "at": utcnow().isoformat(),
                "text": redact_secrets(value)[-2000:],
            })
            self.input_events = self.input_events[-INPUT_EVENT_LIMIT:]

    def wait_for(self, patterns: list[str], *, timeout_ms: int) -> tuple[bool, str | None]:
        deadline = time.time() + timeout_ms / 1000
        while time.time() <= deadline:
            snapshot = self.snapshot()
            combined = f"{snapshot['stdout_tail']}\n{snapshot['stderr_tail']}"
            for pattern in patterns:
                if pattern in combined:
                    return True, pattern
            if snapshot["session_status"] != "running":
                return False, None
            time.sleep(0.05)
        return False, None

    def stop(self) -> None:
        with self.lock:
            self._poll_locked()
            if self._status_locked() != "running":
                return
            self.status_override = "cancelled"
            self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)
        with self.lock:
            self.ended_at = self.ended_at or time.time()
            self._close_transport_locked()

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            self._poll_locked()
            status = self._status_locked()
            exit_code = self.process.returncode
            ended_at = self.ended_at or time.time()
            return {
                "session_id": self.id,
                "session_status": status,
                "transport": self.transport,
                "command": self.command,
                "argv": self.argv,
                "cwd": str(self.cwd),
                "stdout_tail": clean_output(self.stdout.tail(OUTPUT_TAIL_LIMIT)),
                "stderr_tail": clean_output(self.stderr.tail(OUTPUT_TAIL_LIMIT)),
                "exit_code": exit_code,
                "duration_ms": int((ended_at - self.started_at) * 1000),
                "files": list_files(self.cwd),
                "input_events": list(self.input_events),
            }

    def _read_pty(self) -> None:
        assert self.master_fd is not None
        while True:
            try:
                data = os.read(self.master_fd, 1024)
            except OSError:
                break
            if not data:
                break
            self.stdout.append(data.decode("utf-8", errors="replace"))
        with self.lock:
            self._poll_locked()
            self._close_transport_locked()

    def _read_pipe(self, stream: TextIO, buffer: "BoundedText") -> None:
        while True:
            data = stream.read(1)
            if not data:
                break
            buffer.append(data)
        with self.lock:
            self._poll_locked()

    def _poll_locked(self) -> None:
        if self.ended_at is not None:
            return
        return_code = self.process.poll()
        if return_code is not None:
            self.ended_at = time.time()
            self._close_transport_locked()
            return
        if time.time() - self.started_at > self.timeout_seconds:
            self.status_override = "timeout"
            self.process.terminate()

    def _status_locked(self) -> str:
        if self.status_override:
            return self.status_override
        if self.process.poll() is None:
            return "running"
        return "completed" if self.process.returncode == 0 else "failed"

    def _close_transport_locked(self) -> None:
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass
            self.master_fd = None


class BoundedText:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._lock = threading.RLock()
        self._chunks: list[str] = []
        self._length = 0

    def append(self, value: str) -> None:
        if not value:
            return
        with self._lock:
            self._chunks.append(value)
            self._length += len(value)
            while self._length > self.limit and self._chunks:
                removed = self._chunks.pop(0)
                self._length -= len(removed)

    def tail(self, limit: int) -> str:
        with self._lock:
            value = "".join(self._chunks)
        value = value[-limit:] if len(value) > limit else value
        return redact_secrets(value)


TERMINAL_MANAGER = TerminalManager()
