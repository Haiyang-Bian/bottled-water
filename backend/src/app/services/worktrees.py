from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
import re
import subprocess

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, ValidationAppError
from db.models import (
    AgentWorktree,
    ConversationParticipant,
    ConversationRepository,
    utcnow,
)


GIT_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class GitLocation:
    root: Path
    common_dir: Path
    head: str
    branch: str | None


def _canonical(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/").rstrip("/")
    return value.casefold()


def _git_sync(
    root: Path,
    *arguments: str,
    allowed_return_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=GIT_TIMEOUT_SECONDS,
            check=False,
            shell=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValidationAppError(f"Git command could not be started: {exc}") from exc
    if completed.returncode not in allowed_return_codes:
        detail = (completed.stderr or completed.stdout or "Git command failed").strip()
        raise ValidationAppError(detail[-500:])
    return completed


async def _git(
    root: Path,
    *arguments: str,
    allowed_return_codes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    return await asyncio.to_thread(
        _git_sync,
        root,
        *arguments,
        allowed_return_codes=allowed_return_codes,
    )


async def inspect_git_location(raw_path: str | Path) -> GitLocation:
    try:
        requested = Path(raw_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValidationAppError("Repository path does not exist") from exc
    if not requested.is_dir():
        raise ValidationAppError("Repository path must be a directory")
    root_text = (await _git(requested, "rev-parse", "--show-toplevel")).stdout.strip()
    root = Path(root_text).resolve(strict=True)
    common_text = (await _git(root, "rev-parse", "--git-common-dir")).stdout.strip()
    common_candidate = Path(common_text)
    common_dir = (
        common_candidate if common_candidate.is_absolute() else root / common_candidate
    ).resolve(strict=True)
    head = (await _git(root, "rev-parse", "HEAD^{commit}")).stdout.strip()
    branch_result = await _git(
        root,
        "symbolic-ref",
        "--quiet",
        "--short",
        "HEAD",
        allowed_return_codes=(0, 1),
    )
    branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
    return GitLocation(root=root, common_dir=common_dir, head=head, branch=branch)


async def bind_repository(
    db: AsyncSession,
    *,
    conversation_id: str,
    repository_path: str,
    base_commit: str | None = None,
    require_user_approval: bool = False,
) -> ConversationRepository:
    location = await inspect_git_location(repository_path)
    base = (
        await _git(location.root, "rev-parse", f"{base_commit or 'HEAD'}^{{commit}}")
    ).stdout.strip()
    existing = await db.scalar(
        select(ConversationRepository).where(
            ConversationRepository.conversation_id == conversation_id,
            ConversationRepository.deleted_at.is_(None),
        )
    )
    if existing:
        active_worktree = await db.scalar(
            select(AgentWorktree).where(
                AgentWorktree.repository_id == existing.id,
                AgentWorktree.deleted_at.is_(None),
            )
        )
        same_root = _canonical(Path(existing.repository_path)) == _canonical(location.root)
        if not same_root and active_worktree:
            raise ConflictError("Release all Agent worktrees before rebinding the repository")
        existing.repository_path = str(location.root)
        existing.git_common_dir = str(location.common_dir)
        existing.base_commit = base
        existing.require_user_approval = require_user_approval
        existing.status = "active"
        await db.commit()
        await db.refresh(existing)
        return existing

    repository = ConversationRepository(
        conversation_id=conversation_id,
        repository_path=str(location.root),
        git_common_dir=str(location.common_dir),
        base_commit=base,
        require_user_approval=require_user_approval,
        status="active",
    )
    db.add(repository)
    await db.commit()
    await db.refresh(repository)
    return repository


def _short_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9]", "", value).lower()
    return normalized[:8] or "member"


def managed_branch(conversation_id: str, agent_id: str) -> str:
    return f"agenthub/{_short_id(conversation_id)}/{_short_id(agent_id)}"


def managed_worktree_root() -> Path:
    storage = Path(get_settings().storage_dir).expanduser().resolve()
    return storage.parent / "worktrees"


async def _registered_worktree_paths(repository_root: Path) -> set[str]:
    result = await _git(repository_root, "worktree", "list", "--porcelain")
    paths = {
        _canonical(Path(line.removeprefix("worktree ")))
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }
    return paths


async def _ensure_unique_assignment(
    db: AsyncSession, *, path: Path, branch: str, agent_id: str, conversation_id: str
) -> None:
    assignments = (
        await db.scalars(
            select(AgentWorktree).where(AgentWorktree.deleted_at.is_(None))
        )
    ).all()
    for assignment in assignments:
        same_member = (
            assignment.agent_id == agent_id
            and assignment.conversation_id == conversation_id
        )
        if same_member:
            raise ConflictError("This Agent already has a worktree in the Conversation")
        if _canonical(Path(assignment.path)) == _canonical(path):
            raise ConflictError("This worktree path is already assigned")
        if assignment.branch == branch:
            raise ConflictError("This Git branch is already assigned")


async def _ensure_conversation_agent(
    db: AsyncSession, *, conversation_id: str, agent_id: str
) -> None:
    participant = await db.scalar(
        select(ConversationParticipant).where(
            ConversationParticipant.conversation_id == conversation_id,
            ConversationParticipant.agent_id == agent_id,
            ConversationParticipant.left_at.is_(None),
        )
    )
    if not participant:
        raise ValidationAppError("Agent must be an active Conversation member")


async def create_worktree(
    db: AsyncSession,
    *,
    repository: ConversationRepository,
    agent_id: str,
    mode: str,
    adopted_path: str | None = None,
) -> AgentWorktree:
    if mode not in {"managed", "adopted"}:
        raise ValidationAppError("Worktree mode must be managed or adopted")
    await _ensure_conversation_agent(
        db, conversation_id=repository.conversation_id, agent_id=agent_id
    )
    repository_root = Path(repository.repository_path).resolve(strict=True)
    expected_common = _canonical(Path(repository.git_common_dir))
    created_managed = False

    if mode == "managed":
        branch = managed_branch(repository.conversation_id, agent_id)
        root = managed_worktree_root().resolve()
        path = (root / _short_id(repository.conversation_id) / _short_id(agent_id)).resolve()
        if _canonical(path) == _canonical(repository_root):
            raise ValidationAppError("Managed worktree cannot replace the bound repository")
        if path.exists() and any(path.iterdir()):
            raise ConflictError("Managed worktree directory is not empty")
        await _git(repository_root, "check-ref-format", "--branch", branch)
        branch_check = await _git(
            repository_root,
            "show-ref",
            "--verify",
            "--quiet",
            f"refs/heads/{branch}",
            allowed_return_codes=(0, 1, 128),
        )
        if branch_check.returncode == 0:
            raise ConflictError("Managed worktree branch already exists")
        await _ensure_unique_assignment(
            db,
            path=path,
            branch=branch,
            agent_id=agent_id,
            conversation_id=repository.conversation_id,
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        await _git(
            repository_root,
            "worktree",
            "add",
            "-b",
            branch,
            str(path),
            repository.base_commit,
        )
        created_managed = True
        location = await inspect_git_location(path)
    else:
        if not adopted_path:
            raise ValidationAppError("adopted_path is required for adopted worktrees")
        location = await inspect_git_location(adopted_path)
        path = location.root
        branch = location.branch or ""
        if not branch:
            raise ValidationAppError("Detached HEAD worktrees cannot be adopted")
        if _canonical(path) == _canonical(repository_root):
            raise ValidationAppError("The bound user worktree cannot be adopted for an Agent")
        if _canonical(location.common_dir) != expected_common:
            raise ValidationAppError("Adopted worktree must belong to the bound Git repository")
        registered = await _registered_worktree_paths(repository_root)
        if _canonical(path) not in registered:
            raise ValidationAppError("Adopted path is not a registered Git worktree")
        await _ensure_unique_assignment(
            db,
            path=path,
            branch=branch,
            agent_id=agent_id,
            conversation_id=repository.conversation_id,
        )

    assignment = AgentWorktree(
        repository_id=repository.id,
        conversation_id=repository.conversation_id,
        agent_id=agent_id,
        path=str(path),
        branch=branch,
        base_commit=repository.base_commit,
        head_commit=location.head,
        mode=mode,
        status="ready",
        dirty=False,
        merge_status="idle",
        last_error="",
    )
    db.add(assignment)
    try:
        await db.commit()
    except Exception:
        await db.rollback()
        if created_managed:
            try:
                await _git(repository_root, "worktree", "remove", str(path))
            except Exception:
                pass
        raise
    await db.refresh(assignment)
    return assignment


async def refresh_worktree(db: AsyncSession, worktree: AgentWorktree) -> AgentWorktree:
    path = Path(worktree.path)
    if not path.is_dir():
        worktree.status = "missing"
        worktree.dirty = False
        worktree.last_error = "Worktree directory is missing"
        return worktree
    try:
        location = await inspect_git_location(path)
        status = (await _git(path, "status", "--porcelain=v1", "--untracked-files=normal")).stdout
        conflicts = (
            await _git(path, "diff", "--name-only", "--diff-filter=U")
        ).stdout.strip()
        worktree.head_commit = location.head
        worktree.branch = location.branch or worktree.branch
        worktree.dirty = bool(status.strip())
        worktree.merge_status = "conflict" if conflicts else "idle"
        worktree.status = "ready"
        worktree.last_error = ""
    except ValidationAppError as exc:
        worktree.status = "invalid"
        worktree.last_error = exc.message
    return worktree


async def _is_merged_elsewhere(
    db: AsyncSession, repository: ConversationRepository, worktree: AgentWorktree
) -> bool:
    if worktree.head_commit == worktree.base_commit:
        return True
    candidate_heads = {
        (
            await _git(Path(repository.repository_path), "rev-parse", "HEAD^{commit}")
        ).stdout.strip()
    }
    siblings = (
        await db.scalars(
            select(AgentWorktree).where(
                AgentWorktree.repository_id == repository.id,
                AgentWorktree.id != worktree.id,
                AgentWorktree.deleted_at.is_(None),
            )
        )
    ).all()
    candidate_heads.update(item.head_commit for item in siblings if item.head_commit)
    for candidate in candidate_heads:
        result = await _git(
            Path(repository.repository_path),
            "merge-base",
            "--is-ancestor",
            worktree.head_commit,
            candidate,
            allowed_return_codes=(0, 1),
        )
        if result.returncode == 0:
            return True
    return False


async def release_worktree(
    db: AsyncSession,
    *,
    repository: ConversationRepository,
    worktree: AgentWorktree,
) -> None:
    await refresh_worktree(db, worktree)
    if worktree.status != "ready":
        raise ConflictError("Invalid or missing worktree cannot be released automatically")
    if worktree.dirty:
        raise ConflictError("Dirty worktree must be cleaned before release")
    if not await _is_merged_elsewhere(db, repository, worktree):
        raise ConflictError("Worktree contains commits that are not integrated elsewhere")
    if worktree.mode == "managed":
        await _git(Path(repository.repository_path), "worktree", "remove", worktree.path)
    worktree.status = "released"
    worktree.deleted_at = utcnow()
    await db.commit()


def repository_to_dict(repository: ConversationRepository) -> dict:
    return {
        "id": repository.id,
        "conversation_id": repository.conversation_id,
        "repository_path": repository.repository_path,
        "base_commit": repository.base_commit,
        "require_user_approval": repository.require_user_approval,
        "status": repository.status,
        "created_at": repository.created_at.isoformat(),
        "updated_at": repository.updated_at.isoformat(),
    }


def worktree_to_dict(worktree: AgentWorktree) -> dict:
    return {
        "id": worktree.id,
        "conversation_id": worktree.conversation_id,
        "agent_id": worktree.agent_id,
        "path": worktree.path,
        "branch": worktree.branch,
        "base_commit": worktree.base_commit,
        "head_commit": worktree.head_commit,
        "mode": worktree.mode,
        "status": worktree.status,
        "dirty": worktree.dirty,
        "merge_status": worktree.merge_status,
        "last_error": worktree.last_error or None,
        "created_at": worktree.created_at.isoformat(),
        "updated_at": worktree.updated_at.isoformat(),
    }


async def get_repository(
    db: AsyncSession, conversation_id: str
) -> ConversationRepository | None:
    return await db.scalar(
        select(ConversationRepository).where(
            ConversationRepository.conversation_id == conversation_id,
            ConversationRepository.deleted_at.is_(None),
        )
    )


async def get_worktree(
    db: AsyncSession, conversation_id: str, worktree_id: str
) -> AgentWorktree:
    worktree = await db.scalar(
        select(AgentWorktree).where(
            AgentWorktree.id == worktree_id,
            AgentWorktree.conversation_id == conversation_id,
            AgentWorktree.deleted_at.is_(None),
        )
    )
    if not worktree:
        raise NotFoundError("Agent worktree not found")
    return worktree
