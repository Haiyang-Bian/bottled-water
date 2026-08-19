from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.services.tools.execution_root import trusted_execution_root
from db.models import (
    AgentWorktree,
    Conversation,
    ConversationParticipant,
    ConversationRepository,
    User,
)


MAX_GIT_OUTPUT = 120_000


@dataclass(frozen=True)
class TrustedIntegrationApproval:
    approved_by_user_id: str


def _git(
    root: Path,
    *arguments: str,
    allowed_return_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationAppError(f"Git command could not be started: {exc}") from exc
    if result.returncode not in allowed_return_codes:
        detail = (result.stderr or result.stdout or "Git command failed").strip()
        raise ValidationAppError(detail[-1000:])
    return result


def _canonical(path: Path) -> str:
    return str(path.resolve()).replace("\\", "/").rstrip("/").casefold()


def _relative_path(value: Any) -> str:
    raw = str(value or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts:
        raise ValidationAppError("Git path must be a relative path inside the Agent worktree")
    if raw == ".git" or raw.startswith(".git/"):
        raise ValidationAppError("Git metadata paths are not accepted")
    return path.as_posix()


def _caller_assignment(
    db: Session, user: User, arguments: dict[str, Any]
) -> tuple[ConversationRepository, AgentWorktree, Path]:
    conversation_id = str(arguments.get("conversation_id") or "")
    agent_id = str(arguments.get("agent_id") or "")
    root = trusted_execution_root(arguments)
    if root is None:
        raise ValidationAppError("Git tools require a trusted Agent execution root")
    conversation = db.get(Conversation, conversation_id)
    if not conversation or conversation.deleted_at is not None:
        raise NotFoundError("Conversation not found")
    has_access = conversation.creator_id == user.id or user.role == "admin"
    if not has_access:
        has_access = (
            db.scalar(
                select(ConversationParticipant.id).where(
                    ConversationParticipant.conversation_id == conversation_id,
                    ConversationParticipant.user_id == user.id,
                    ConversationParticipant.left_at.is_(None),
                )
            )
            is not None
        )
    if not has_access:
        raise ForbiddenError("No access to this Conversation repository")
    repository = db.scalar(
        select(ConversationRepository).where(
            ConversationRepository.conversation_id == conversation_id,
            ConversationRepository.deleted_at.is_(None),
            ConversationRepository.status == "active",
        )
    )
    if not repository:
        raise NotFoundError("Conversation repository not found")
    assignment = db.scalar(
        select(AgentWorktree).where(
            AgentWorktree.repository_id == repository.id,
            AgentWorktree.agent_id == agent_id,
            AgentWorktree.deleted_at.is_(None),
            AgentWorktree.status == "ready",
        )
    )
    if not assignment or _canonical(Path(assignment.path)) != _canonical(root):
        raise ForbiddenError("Git tool is not bound to this Agent worktree")
    return repository, assignment, root


def invoke_git_tool(
    db: Session, user: User, name: str, arguments: dict[str, Any]
) -> dict[str, Any]:
    repository, assignment, root = _caller_assignment(db, user, arguments)
    if name == "git.status":
        return _status(root, assignment)
    if name == "git.diff":
        return _diff(root, arguments)
    if name == "git.commit":
        return _commit(root, assignment, arguments)
    if name == "git.integrate":
        return _integrate(db, user, repository, assignment, root, arguments)
    raise NotFoundError("Git collaboration tool not found")


def _status(root: Path, assignment: AgentWorktree) -> dict[str, Any]:
    porcelain = _git(root, "status", "--short", "--branch").stdout
    head = _git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
    assignment.head_commit = head
    assignment.dirty = bool(
        [line for line in porcelain.splitlines() if not line.startswith("##")]
    )
    assignment.merge_status = "idle"
    db_status = {
        "status": "succeeded",
        "branch": assignment.branch,
        "head_commit": head,
        "dirty": assignment.dirty,
        "summary": porcelain[:MAX_GIT_OUTPUT],
    }
    return db_status


def _diff(root: Path, arguments: dict[str, Any]) -> dict[str, Any]:
    command = ["diff"]
    if bool(arguments.get("staged")):
        command.append("--cached")
    path_value = arguments.get("path")
    if path_value:
        command.extend(["--", _relative_path(path_value)])
    result = _git(root, *command)
    return {
        "status": "succeeded",
        "staged": bool(arguments.get("staged")),
        "patch": result.stdout[:MAX_GIT_OUTPUT],
        "truncated": len(result.stdout) > MAX_GIT_OUTPUT,
    }


def _commit(
    root: Path, assignment: AgentWorktree, arguments: dict[str, Any]
) -> dict[str, Any]:
    message = str(arguments.get("message") or "").strip()
    if not message or len(message) > 200 or "\n" in message or "\r" in message:
        raise ValidationAppError("Commit message must be a single line of at most 200 characters")
    raw_paths = arguments.get("paths")
    if raw_paths:
        if not isinstance(raw_paths, list) or len(raw_paths) > 200:
            raise ValidationAppError("paths must be a list with at most 200 entries")
        paths = [_relative_path(item) for item in raw_paths]
        _git(root, "add", "--", *paths)
    else:
        _git(root, "add", "-A")
    staged = _git(root, "diff", "--cached", "--quiet", allowed_return_codes=(0, 1))
    if staged.returncode == 0:
        raise ValidationAppError("There are no staged changes to commit")
    _git(
        root,
        "-c",
        f"user.name=AgentHub {assignment.agent_id[:8]}",
        "-c",
        "user.email=agent@agenthub.local",
        "commit",
        "-m",
        message,
    )
    head = _git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
    assignment.head_commit = head
    assignment.dirty = False
    return {
        "status": "succeeded",
        "branch": assignment.branch,
        "head_commit": head,
        "message": message,
    }


def _integrate(
    db: Session,
    user: User,
    repository: ConversationRepository,
    target: AgentWorktree,
    root: Path,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    source_agent_id = str(arguments.get("source_agent_id") or "").strip()
    if not source_agent_id or source_agent_id == target.agent_id:
        raise ValidationAppError("source_agent_id must identify a different team member")
    source = db.scalar(
        select(AgentWorktree).where(
            AgentWorktree.repository_id == repository.id,
            AgentWorktree.conversation_id == target.conversation_id,
            AgentWorktree.agent_id == source_agent_id,
            AgentWorktree.deleted_at.is_(None),
            AgentWorktree.status == "ready",
        )
    )
    if not source:
        raise ValidationAppError("Source Agent has no ready worktree in this Conversation")
    if repository.require_user_approval:
        approval = arguments.get("_trusted_integration_approval")
        if not isinstance(approval, TrustedIntegrationApproval):
            return {
                "status": "approval_required",
                "source_agent_id": source_agent_id,
                "source_branch": source.branch,
            }
        if approval.approved_by_user_id != user.id and user.role != "admin":
            raise ForbiddenError("Integration approval belongs to another user")
    target_status = _git(root, "status", "--porcelain=v1").stdout.strip()
    if target_status:
        raise ValidationAppError("Target Agent worktree must be clean before integration")
    source_status = _git(Path(source.path), "status", "--porcelain=v1").stdout.strip()
    if source_status:
        raise ValidationAppError("Source Agent worktree must be clean before integration")

    merge = _git(
        root,
        "-c",
        f"user.name=AgentHub {target.agent_id[:8]}",
        "-c",
        "user.email=agent@agenthub.local",
        "merge",
        "--no-ff",
        "--no-edit",
        source.branch,
        allowed_return_codes=(0, 1),
    )
    if merge.returncode == 0:
        head = _git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
        target.head_commit = head
        target.merge_status = "merged"
        target.last_error = ""
        return {
            "status": "succeeded",
            "source_agent_id": source_agent_id,
            "source_branch": source.branch,
            "target_branch": target.branch,
            "head_commit": head,
        }

    conflicts = [
        line
        for line in _git(root, "diff", "--name-only", "--diff-filter=U").stdout.splitlines()
        if line
    ]
    abort = _git(root, "merge", "--abort", allowed_return_codes=(0, 1, 128))
    if abort.returncode != 0:
        target.merge_status = "abort_failed"
        target.last_error = (abort.stderr or abort.stdout)[-1000:]
        raise ValidationAppError("Merge conflicted and automatic merge abort failed")
    target.merge_status = "conflict"
    target.last_error = f"Conflict integrating {source.branch}: {', '.join(conflicts)}"
    return {
        "status": "conflict",
        "source_agent_id": source_agent_id,
        "source_branch": source.branch,
        "target_branch": target.branch,
        "conflict_files": conflicts,
        "merge_aborted": True,
        "discussion_message": (
            f"Integration of {source.branch} into {target.branch} conflicted in: "
            + (", ".join(conflicts) or "unknown files")
        ),
    }
