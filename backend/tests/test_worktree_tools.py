from __future__ import annotations

from pathlib import Path
import subprocess

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from agent_runtime.core.types import ToolCall
from agent_runtime.runtime.team_tools import TeamToolExecutor
from app.core.errors import ValidationAppError
from app.services.tools.execution_root import (
    TrustedExecutionRoot,
    trusted_execution_path,
    trusted_execution_root,
)
from app.services.tools.git_collaboration import (
    TrustedIntegrationApproval,
    invoke_git_tool,
)
from app.services.external_agents.workspace import external_agent_cwd
from app.services.tools.builtins.file.executor import invoke_file_tool
from app.services.tools.builtins.sandbox.executor import _session_and_cwd as sandbox_cwd
from app.services.tools.builtins.terminal.executor import _session_and_cwd as terminal_cwd
from db.base import Base
from db.models import (
    Agent,
    AgentWorktree,
    Conversation,
    ConversationParticipant,
    ConversationRepository,
    User,
)


pytestmark = [pytest.mark.unit, pytest.mark.worktrees]


def _git(root: Path, *arguments: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=check,
        shell=False,
    )
    return result.stdout.strip()


def _commit(root: Path, filename: str, content: str, message: str) -> str:
    (root / filename).write_text(content, encoding="utf-8")
    _git(root, "add", filename)
    _git(root, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", message)
    return _git(root, "rev-parse", "HEAD")


def _tool_database(tmp_path: Path, *, require_approval: bool = False):
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init")
    _commit(repository, "shared.txt", "base\n", "initial")
    base = _git(repository, "rev-parse", "HEAD")
    author_path = tmp_path / "author"
    reviewer_path = tmp_path / "reviewer"
    _git(repository, "worktree", "add", "-b", "agenthub/team/author", str(author_path), base)
    _git(repository, "worktree", "add", "-b", "agenthub/team/reviewer", str(reviewer_path), base)

    engine = create_engine(f"sqlite:///{tmp_path / 'tools.db'}")
    Base.metadata.create_all(engine)
    db = Session(engine)
    db.add_all(
        [
            User(
                id="user",
                email="git@example.com",
                username="git-user",
                password_hash="x",
            ),
            Agent(id="author", name="Author", type="custom", owner_id="user"),
            Agent(id="reviewer", name="Reviewer", type="custom", owner_id="user"),
            Conversation(
                id="conversation",
                creator_id="user",
                chat_type="group",
                title="Team",
                extra={},
            ),
            ConversationParticipant(
                id="author-participant",
                conversation_id="conversation",
                participant_type="agent",
                agent_id="author",
            ),
            ConversationParticipant(
                id="reviewer-participant",
                conversation_id="conversation",
                participant_type="agent",
                agent_id="reviewer",
            ),
            ConversationRepository(
                id="repository-record",
                conversation_id="conversation",
                repository_path=str(repository),
                git_common_dir=str(repository / ".git"),
                base_commit=base,
                require_user_approval=require_approval,
            ),
            AgentWorktree(
                id="author-worktree",
                repository_id="repository-record",
                conversation_id="conversation",
                agent_id="author",
                path=str(author_path),
                branch="agenthub/team/author",
                base_commit=base,
                head_commit=base,
                mode="managed",
            ),
            AgentWorktree(
                id="reviewer-worktree",
                repository_id="repository-record",
                conversation_id="conversation",
                agent_id="reviewer",
                path=str(reviewer_path),
                branch="agenthub/team/reviewer",
                base_commit=base,
                head_commit=base,
                mode="managed",
            ),
        ]
    )
    db.commit()
    return db, repository, author_path, reviewer_path


def _arguments(root: Path, agent_id: str, **values):
    return {
        "conversation_id": "conversation",
        "agent_id": agent_id,
        "_trusted_execution_root": TrustedExecutionRoot(root),
        **values,
    }


def test_execution_root_requires_in_process_capability_and_rejects_escape(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    assert trusted_execution_root({"_trusted_execution_root": str(root)}) is None
    assert trusted_execution_path(
        {"_trusted_execution_root": TrustedExecutionRoot(root)}, "src/main.py"
    ) == root / "src" / "main.py"
    with pytest.raises(ValidationAppError, match="absolute paths"):
        trusted_execution_path(
            {"_trusted_execution_root": TrustedExecutionRoot(root)}, str(tmp_path / "outside")
        )
    with pytest.raises(ValidationAppError, match="traversal"):
        trusted_execution_path(
            {"_trusted_execution_root": TrustedExecutionRoot(root)}, "../outside"
        )


def test_git_commit_only_changes_callers_branch(tmp_path):
    db, repository, author_path, _ = _tool_database(tmp_path)
    try:
        user = db.get(User, "user")
        main_head = _git(repository, "rev-parse", "HEAD")
        (author_path / "author.txt").write_text("isolated\n", encoding="utf-8")
        result = invoke_git_tool(
            db,
            user,
            "git.commit",
            _arguments(author_path, "author", message="author change"),
        )
        db.commit()

        assert result["status"] == "succeeded"
        assert result["head_commit"] != main_head
        assert _git(repository, "rev-parse", "HEAD") == main_head
        assert not (repository / "author.txt").exists()
    finally:
        db.close()


def test_file_sandbox_terminal_and_external_agent_share_trusted_root(tmp_path):
    db, _, author_path, _ = _tool_database(tmp_path)
    try:
        user = db.get(User, "user")
        arguments = _arguments(author_path, "author")
        written = invoke_file_tool(
            db,
            user,
            "file.write",
            {**arguments, "path": "notes/agent.txt", "content": "owned\n"},
        )
        _, sandbox_path = sandbox_cwd(db, user, {**arguments, "workdir": "src"})
        _, terminal_path, terminal_root = terminal_cwd(
            db, user, {**arguments, "workdir": "tests"}
        )
        _, external_path = external_agent_cwd(db, arguments, provider="codex")

        assert written["status"] == "succeeded"
        assert (author_path / "notes" / "agent.txt").read_text(encoding="utf-8") == "owned\n"
        assert sandbox_path == author_path / "src"
        assert terminal_path == author_path / "tests"
        assert terminal_root == author_path
        assert external_path == author_path
    finally:
        db.close()


def test_git_integrate_merges_only_same_conversation_member_branch(tmp_path):
    db, repository, author_path, reviewer_path = _tool_database(tmp_path)
    try:
        user = db.get(User, "user")
        main_head = _git(repository, "rev-parse", "HEAD")
        source_head = _commit(author_path, "feature.txt", "feature\n", "author feature")
        source = db.get(AgentWorktree, "author-worktree")
        source.head_commit = source_head
        db.commit()

        result = invoke_git_tool(
            db,
            user,
            "git.integrate",
            _arguments(reviewer_path, "reviewer", source_agent_id="author"),
        )
        db.commit()

        assert result["status"] == "succeeded"
        assert (reviewer_path / "feature.txt").read_text(encoding="utf-8") == "feature\n"
        assert _git(repository, "rev-parse", "HEAD") == main_head
        assert _git(reviewer_path, "merge-base", "--is-ancestor", source_head, "HEAD") == ""
    finally:
        db.close()


def test_git_conflict_aborts_and_restores_clean_target(tmp_path):
    db, _, author_path, reviewer_path = _tool_database(tmp_path)
    try:
        user = db.get(User, "user")
        author_head = _commit(author_path, "shared.txt", "author\n", "author edit")
        reviewer_head = _commit(reviewer_path, "shared.txt", "reviewer\n", "reviewer edit")
        db.get(AgentWorktree, "author-worktree").head_commit = author_head
        db.get(AgentWorktree, "reviewer-worktree").head_commit = reviewer_head
        db.commit()

        result = invoke_git_tool(
            db,
            user,
            "git.integrate",
            _arguments(reviewer_path, "reviewer", source_agent_id="author"),
        )

        assert result["status"] == "conflict"
        assert result["merge_aborted"] is True
        assert result["conflict_files"] == ["shared.txt"]
        assert _git(reviewer_path, "rev-parse", "HEAD") == reviewer_head
        assert _git(reviewer_path, "status", "--porcelain") == ""
    finally:
        db.close()


def test_optional_user_approval_hook_blocks_agent_integration(tmp_path):
    db, _, author_path, reviewer_path = _tool_database(tmp_path, require_approval=True)
    try:
        user = db.get(User, "user")
        source_head = _commit(author_path, "approved.txt", "ready\n", "approval source")
        db.get(AgentWorktree, "author-worktree").head_commit = source_head
        db.commit()
        arguments = _arguments(reviewer_path, "reviewer", source_agent_id="author")

        pending = invoke_git_tool(db, user, "git.integrate", arguments)
        approved = invoke_git_tool(
            db,
            user,
            "git.integrate",
            {
                **arguments,
                "_trusted_integration_approval": TrustedIntegrationApproval("user"),
            },
        )

        assert pending["status"] == "approval_required"
        assert approved["status"] == "succeeded"
    finally:
        db.close()


class ConflictBaseExecutor:
    async def list_tools(self):
        return []

    async def execute(self, tool_call):
        return {
            "type": "tool",
            "tool_name": "git.integrate",
            "status": "conflict",
            "output": {
                "status": "conflict",
                "source_agent_id": "author",
                "discussion_message": "Conflict in shared.txt",
            },
        }


class RecordingMessenger:
    def __init__(self) -> None:
        self.calls = []

    async def send_message(self, **values):
        self.calls.append(values)
        return type("Message", (), {"message_id": "message", "sequence": 7})()


async def test_conflict_result_is_handed_back_through_team_message():
    messenger = RecordingMessenger()
    executor = TeamToolExecutor(ConflictBaseExecutor(), messenger, "reviewer")

    result = await executor.execute(
        ToolCall(
            tool_name="git.integrate",
            parameters={"source_agent_id": "author"},
            call_id="call",
        )
    )

    assert result["output"]["team_message_id"] == "message"
    assert messenger.calls == [
        {
            "sender_agent_id": "reviewer",
            "recipient_agent_ids": ("author",),
            "content": "Conflict in shared.txt",
            "expects_reply": True,
        }
    ]
