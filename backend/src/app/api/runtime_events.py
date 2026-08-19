"""Authenticated Runtime Event Log queries for reconnect and diagnostics."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.core.response import ok
from app.deps import get_current_user
from app.persistence.runtime_journal import SQLRunJournal
from app.persistence.team_journal import SQLTeamJournal
from app.schemas.common import ApiResponse
from app.services.runtime.event_projection import project_runtime_event
from db import get_db
from db.models import Conversation, ConversationParticipant, RuntimeRun, User


router = APIRouter(tags=["runtime-events"])


@router.get(
    "/conversations/{conversation_id}/runtime/runs/{run_id}/events",
    response_model=ApiResponse[dict],
)
async def list_runtime_events(
    conversation_id: str,
    run_id: str,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
            or_(
                Conversation.creator_id == user.id,
                Conversation.participants.any(
                    and_(
                        ConversationParticipant.user_id == user.id,
                        ConversationParticipant.left_at.is_(None),
                    )
                ),
            ),
        )
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")
    run = await db.get(RuntimeRun, run_id)
    if run is None or str(run.context_scope_id) != conversation_id:
        raise NotFoundError("Runtime run not found")
    page = await SQLRunJournal.read_events_with_session(
        db,
        run_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return ok(
        {
            "run_id": run_id,
            "context_scope_id": conversation_id,
            "items": [_event_dict(event) for event in page.items],
            "next_sequence": page.next_sequence,
            "last_sequence": page.last_sequence,
            "terminal": page.terminal,
            "history_complete": page.history_complete,
        }
    )


def _event_dict(event) -> dict:  # noqa: ANN001
    projected = project_runtime_event(event, replayed=True)
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "context_scope_id": event.context_scope_id,
        "sequence": event.sequence,
        "type": event.type,
        "source": event.source,
        "target": event.target,
        "payload": event.payload,
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
        "occurred_at": event.occurred_at.isoformat(),
        "projected_type": projected.type,
        "projected_payload": projected.payload,
    }


@router.get(
    "/conversations/{conversation_id}/team/messages",
    response_model=ApiResponse[dict],
)
async def list_team_messages(
    conversation_id: str,
    after_sequence: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=500),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.deleted_at.is_(None),
            or_(
                Conversation.creator_id == user.id,
                Conversation.participants.any(
                    and_(
                        ConversationParticipant.user_id == user.id,
                        ConversationParticipant.left_at.is_(None),
                    )
                ),
            ),
        )
    )
    if conversation is None:
        raise NotFoundError("Conversation not found")
    page = await SQLTeamJournal.read_messages_with_session(
        db,
        conversation_id,
        after_sequence=after_sequence,
        limit=limit,
    )
    return ok(
        {
            "conversation_id": conversation_id,
            "items": [_team_message_dict(item) for item in page.items],
            "next_sequence": page.next_sequence,
            "last_sequence": page.last_sequence,
        }
    )


def _team_message_dict(message) -> dict:  # noqa: ANN001
    return {
        "message_id": message.message_id,
        "run_id": message.run_id,
        "conversation_id": message.context_scope_id,
        "sequence": message.sequence,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "recipient_agent_ids": list(message.recipient_agent_ids),
        "channel": message.channel,
        "thread_id": message.thread_id,
        "reply_to_message_id": message.reply_to_message_id,
        "content": message.content,
        "expects_reply": message.expects_reply,
        "status": message.status,
        "consumed_by": list(message.consumed_by),
        "created_at": message.created_at.isoformat(),
        "resolved_at": message.resolved_at.isoformat() if message.resolved_at else None,
    }
