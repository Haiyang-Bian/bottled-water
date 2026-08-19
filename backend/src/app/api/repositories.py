from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.conversations import _ensure_can_manage, _get
from app.core.errors import NotFoundError, ValidationAppError
from app.core.response import ok
from app.deps import get_current_user
from app.schemas.common import ApiResponse
from app.schemas.requests import (
    BindConversationRepositoryRequest,
    CreateAgentWorktreeRequest,
    IntegrateAgentWorktreeRequest,
)
from app.services.worktrees import (
    bind_repository,
    create_worktree,
    get_repository,
    get_worktree,
    refresh_worktree,
    release_worktree,
    repository_to_dict,
    worktree_to_dict,
)
from db import get_db
from db.models import AgentWorktree, User
from app.services.tools.execution_root import TrustedExecutionRoot
from app.services.tools.git_collaboration import (
    TrustedIntegrationApproval,
    invoke_git_tool,
)
from pathlib import Path


router = APIRouter(tags=["conversation-repositories"])


async def _repository_view(db: AsyncSession, conversation_id: str) -> dict:
    repository = await get_repository(db, conversation_id)
    if not repository:
        return {"repository": None, "worktrees": []}
    worktrees = (
        await db.scalars(
            select(AgentWorktree)
            .where(
                AgentWorktree.repository_id == repository.id,
                AgentWorktree.deleted_at.is_(None),
            )
            .order_by(AgentWorktree.created_at.asc())
        )
    ).all()
    for worktree in worktrees:
        await refresh_worktree(db, worktree)
    await db.commit()
    return {
        "repository": repository_to_dict(repository),
        "worktrees": [worktree_to_dict(item) for item in worktrees],
    }


@router.get(
    "/conversations/{conversation_id}/repository", response_model=ApiResponse[dict]
)
async def conversation_repository(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await _get(db, user, conversation_id)
    return ok(await _repository_view(db, conversation_id))


@router.put(
    "/conversations/{conversation_id}/repository", response_model=ApiResponse[dict]
)
async def bind_conversation_repository(
    conversation_id: str,
    payload: BindConversationRepositoryRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    active_agents = [
        item
        for item in conversation.participants
        if item.agent_id and item.left_at is None
    ]
    if conversation.chat_type != "group" or len(active_agents) < 2:
        raise ValidationAppError("Repository worktrees require a group Conversation")
    await bind_repository(
        db,
        conversation_id=conversation_id,
        repository_path=payload.repository_path,
        base_commit=payload.base_commit,
        require_user_approval=payload.require_user_approval,
    )
    return ok(await _repository_view(db, conversation_id), "Repository bound")


@router.post(
    "/conversations/{conversation_id}/worktrees", response_model=ApiResponse[dict]
)
async def add_agent_worktree(
    conversation_id: str,
    payload: CreateAgentWorktreeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    repository = await get_repository(db, conversation_id)
    if not repository:
        raise NotFoundError("Bind a repository before creating Agent worktrees")
    await create_worktree(
        db,
        repository=repository,
        agent_id=payload.agent_id,
        mode=payload.mode,
        adopted_path=payload.path,
    )
    return ok(await _repository_view(db, conversation_id), "Agent worktree created")


@router.delete(
    "/conversations/{conversation_id}/worktrees/{worktree_id}",
    response_model=ApiResponse[dict],
)
async def remove_agent_worktree(
    conversation_id: str,
    worktree_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    repository = await get_repository(db, conversation_id)
    if not repository:
        raise NotFoundError("Conversation repository not found")
    worktree = await get_worktree(db, conversation_id, worktree_id)
    await release_worktree(db, repository=repository, worktree=worktree)
    return ok(await _repository_view(db, conversation_id), "Agent worktree released")


@router.post(
    "/conversations/{conversation_id}/worktrees/{worktree_id}/integrate",
    response_model=ApiResponse[dict],
)
async def approve_agent_worktree_integration(
    conversation_id: str,
    worktree_id: str,
    payload: IntegrateAgentWorktreeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await _get(db, user, conversation_id)
    _ensure_can_manage(conversation, user)
    target = await get_worktree(db, conversation_id, worktree_id)
    result = await db.run_sync(
        lambda session: invoke_git_tool(
            session,
            user,
            "git.integrate",
            {
                "conversation_id": conversation_id,
                "agent_id": target.agent_id,
                "source_agent_id": payload.source_agent_id,
                "_trusted_execution_root": TrustedExecutionRoot(Path(target.path)),
                "_trusted_integration_approval": TrustedIntegrationApproval(user.id),
            },
        )
    )
    await db.commit()
    return ok(
        {"integration": result, **(await _repository_view(db, conversation_id))},
        "Integration completed" if result.get("status") == "succeeded" else "Integration checked",
    )
