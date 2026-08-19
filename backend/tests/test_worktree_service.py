from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.errors import ConflictError, ValidationAppError
from app.services import worktrees as worktree_service
from app.services.worktrees import (
    bind_repository,
    create_worktree,
    inspect_git_location,
    managed_branch,
    refresh_worktree,
    release_worktree,
)
from db.base import Base
from db.models import Agent, Conversation, ConversationParticipant, User


pytestmark = [pytest.mark.integration, pytest.mark.worktrees]


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        shell=False,
    )
    return result.stdout.strip()


def _repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _git(repository, "config", "user.name", "AgentHub Test")
    _git(repository, "config", "user.email", "agenthub@example.com")
    (repository / "README.md").write_text("base\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-m", "initial")
    return repository


async def _database(tmp_path: Path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'worktrees.db'}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add_all(
            [
                User(
                    id="user",
                    email="worktrees@example.com",
                    username="worktrees",
                    password_hash="x",
                ),
                Agent(id="agent-a", name="Agent A", type="custom", owner_id="user"),
                Agent(id="agent-b", name="Agent B", type="custom", owner_id="user"),
                Conversation(
                    id="conversation",
                    creator_id="user",
                    chat_type="group",
                    title="Team",
                    extra={},
                ),
                ConversationParticipant(
                    id="participant-a",
                    conversation_id="conversation",
                    participant_type="agent",
                    agent_id="agent-a",
                ),
                ConversationParticipant(
                    id="participant-b",
                    conversation_id="conversation",
                    participant_type="agent",
                    agent_id="agent-b",
                ),
            ]
        )
        await db.commit()
    return engine, factory


async def test_managed_worktrees_use_isolated_branches_and_do_not_move_user_head(
    tmp_path, monkeypatch
):
    repository_path = _repository(tmp_path)
    original_head = _git(repository_path, "rev-parse", "HEAD")
    engine, factory = await _database(tmp_path)
    monkeypatch.setattr(
        worktree_service, "managed_worktree_root", lambda: tmp_path / "managed"
    )
    try:
        async with factory() as db:
            repository = await bind_repository(
                db,
                conversation_id="conversation",
                repository_path=str(repository_path),
            )
            first = await create_worktree(
                db, repository=repository, agent_id="agent-a", mode="managed"
            )
            second = await create_worktree(
                db, repository=repository, agent_id="agent-b", mode="managed"
            )

            assert first.branch == managed_branch("conversation", "agent-a")
            assert second.branch == managed_branch("conversation", "agent-b")
            assert Path(first.path) != Path(second.path)
            assert (await inspect_git_location(first.path)).common_dir == (
                await inspect_git_location(second.path)
            ).common_dir
            assert _git(repository_path, "rev-parse", "HEAD") == original_head
            raw_repository_path = (
                await db.execute(
                    text(
                        "SELECT repository_path FROM conversation_repositories "
                        "WHERE id = :repository_id"
                    ),
                    {"repository_id": repository.id},
                )
            ).scalar_one()
            raw_worktree_path = (
                await db.execute(
                    text("SELECT path FROM agent_worktrees WHERE id = :worktree_id"),
                    {"worktree_id": first.id},
                )
            ).scalar_one()
            assert str(repository_path) not in raw_repository_path
            assert first.path not in raw_worktree_path
    finally:
        await engine.dispose()


async def test_adopted_worktree_must_be_separate_and_unique(tmp_path):
    repository_path = _repository(tmp_path)
    adopted_path = tmp_path / "adopted"
    _git(repository_path, "worktree", "add", "-b", "adopted-agent", str(adopted_path))
    engine, factory = await _database(tmp_path)
    try:
        async with factory() as db:
            repository = await bind_repository(
                db,
                conversation_id="conversation",
                repository_path=str(repository_path),
            )
            with pytest.raises(ValidationAppError, match="bound user worktree"):
                await create_worktree(
                    db,
                    repository=repository,
                    agent_id="agent-a",
                    mode="adopted",
                    adopted_path=str(repository_path),
                )
            await create_worktree(
                db,
                repository=repository,
                agent_id="agent-a",
                mode="adopted",
                adopted_path=str(adopted_path),
            )
            with pytest.raises(ConflictError, match="already assigned"):
                await create_worktree(
                    db,
                    repository=repository,
                    agent_id="agent-b",
                    mode="adopted",
                    adopted_path=str(adopted_path),
                )
    finally:
        await engine.dispose()


async def test_release_refuses_dirty_or_unintegrated_managed_worktree(tmp_path, monkeypatch):
    repository_path = _repository(tmp_path)
    engine, factory = await _database(tmp_path)
    monkeypatch.setattr(
        worktree_service, "managed_worktree_root", lambda: tmp_path / "managed"
    )
    try:
        async with factory() as db:
            repository = await bind_repository(
                db,
                conversation_id="conversation",
                repository_path=str(repository_path),
            )
            worktree = await create_worktree(
                db, repository=repository, agent_id="agent-a", mode="managed"
            )
            worktree_path = Path(worktree.path)
            (worktree_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with pytest.raises(ConflictError, match="Dirty worktree"):
                await release_worktree(db, repository=repository, worktree=worktree)

            (worktree_path / "dirty.txt").unlink()
            (worktree_path / "feature.txt").write_text("feature\n", encoding="utf-8")
            _git(worktree_path, "add", "feature.txt")
            _git(worktree_path, "commit", "-m", "feature")
            await refresh_worktree(db, worktree)
            with pytest.raises(ConflictError, match="not integrated"):
                await release_worktree(db, repository=repository, worktree=worktree)
    finally:
        await engine.dispose()
