import asyncio
import json
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from app.core.errors import NotFoundError, ValidationAppError
from app.core.response import ok
from app.deps import get_current_user
from db import get_db
from db.models import Conversation, ConversationParticipant, FileAsset, Message, User, utcnow
from app.schemas.common import ApiResponse
from app.schemas.requests import RunMessageCodeRequest, SendMessagePayload
from app.events import SseSink
from app.events import app_event_bus as event_bus
from app.services.chat.mentions import normalize_agent_mentions, raw_agent_mentions
from app.services.chat.scheduling import persist_scheduling_strategy, resolve_scheduling_strategy
from app.services.chat.user_messages import message_text, save_user_message
from app.services.serialization import message_to_dict
from app.services.files.attachments import (
    attachment_from_file_asset,
    refresh_attachment_text_if_needed,
)
from app.services.files.references import file_reference_text, resolve_file_reference_attachments
from app.services.chat.code_runner import run_message_code_block
from app.services.chat.message_prompt import agent_mentions_for_message, runtime_prompt_for_message


router = APIRouter(tags=["messages"])
compat_router = APIRouter(tags=["messages-compat"])
ORCHESTRATION_TASKS: dict[str, asyncio.Task] = {}


def _conversation_access_filter(user: User):
    return or_(
        Conversation.creator_id == user.id,
        Conversation.participants.any(
            and_(
                ConversationParticipant.user_id == user.id,
                ConversationParticipant.left_at.is_(None),
            )
        ),
    )


async def _get_conversation(db: AsyncSession, user: User, conversation_id: str) -> Conversation:
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            _conversation_access_filter(user),
            Conversation.deleted_at.is_(None),
        )
    )
    if not conversation:
        raise NotFoundError("Conversation not found")
    return conversation


def _message_text(payload: dict) -> str:
    return message_text(payload)


async def _list_messages(db: AsyncSession, user: User, conversation_id: str) -> list[dict]:
    await _get_conversation(db, user, conversation_id)
    messages = (
        await db.scalars(
            select(Message)
            .where(Message.conversation_id == conversation_id, Message.deleted_at.is_(None))
            .order_by(Message.created_at.asc())
        )
    ).all()
    return [message_to_dict(message) for message in messages]


async def _send_async(
    db: AsyncSession, user: User, conversation_id: str, payload: dict, *, trigger_agent: bool = True
) -> Message:
    conversation = await _get_conversation(db, user, conversation_id)
    message = await save_user_message(db, user=user, conversation=conversation, payload=payload)
    text = _message_text(payload).strip()

    if trigger_agent:
        from app.services.conversation_session_manager import ConversationRunManager

        session_manager = ConversationRunManager.get_instance()
        await session_manager.get_or_create_engine(
            db,
            conversation,
            model_config_id=payload.get("model_config_id"),
            event_sink=SseSink(str(conversation.id)),
        )
        await session_manager.start_generation(
            conversation.id,
            text,
            runtime_content=runtime_prompt_for_message(message),
            thinking_enabled=bool(payload.get("thinking_enabled")),
            user_message_id=str(message.id),
            client_message_id=message.client_message_id,
            agent_mentions=agent_mentions_for_message(message),
        )

    return message


def _send(
    db, user: User, conversation_id: str, payload: dict, *, trigger_agent: bool = True
):
    if hasattr(db, "run_sync"):
        return _send_async(db, user, conversation_id, payload, trigger_agent=trigger_agent)
    return _send_sync(db, user, conversation_id, payload, trigger_agent=trigger_agent)


def _send_sync(db, user: User, conversation_id: str, payload: dict, *, trigger_agent: bool = True) -> Message:
    conversation = db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            _conversation_access_filter(user),
            Conversation.deleted_at.is_(None),
        )
    )
    if not conversation:
        raise NotFoundError("Conversation not found")
    text = _message_text(payload).strip()
    if not text:
        raise ValidationAppError("Message content cannot be empty")

    raw_content = payload.get("content") if isinstance(payload.get("content"), dict) else {}
    attachments = raw_content.get("attachments") or payload.get("attachments") or []
    normalized_attachments: list[dict] = []
    for item in attachments:
        file_id = item.get("file_id") or item.get("id") if isinstance(item, dict) else str(item)
        if not file_id:
            continue
        file_asset = db.scalar(
            select(FileAsset).where(
                FileAsset.id == file_id,
                FileAsset.owner_id == user.id,
                FileAsset.deleted_at.is_(None),
            )
        )
        if file_asset:
            refresh_attachment_text_if_needed(file_asset)
            normalized_attachments.append(attachment_from_file_asset(file_asset))
    existing_file_ids = {str(item.get("file_id") or "") for item in normalized_attachments}
    reference_text = file_reference_text(
        text,
        raw_content.get("file_references") or payload.get("file_references"),
    )
    if reference_text:
        normalized_attachments.extend(
            resolve_file_reference_attachments(
                db,
                user=user,
                conversation=conversation,
                text=reference_text,
                existing_file_ids=existing_file_ids,
            )
        )
    normalized_agent_mentions = normalize_agent_mentions(
        db,
        conversation_id=conversation.id,
        mentions=raw_agent_mentions(payload, raw_content),
    )

    scheduling_strategy = resolve_scheduling_strategy(conversation, payload.get("scheduling_strategy"))
    if payload.get("scheduling_strategy"):
        persist_scheduling_strategy(conversation, scheduling_strategy)
    message = Message(
        client_message_id=payload.get("client_message_id") or str(uuid.uuid4()),
        conversation_id=conversation.id,
        sender_type="user",
        sender_id=user.id,
        sender_name=user.display_name,
        sender_avatar_url=user.avatar_url,
        content_type=payload.get("content_type") or "text",
        content={
            "text": text,
            "attachments": normalized_attachments,
            "agent_mentions": normalized_agent_mentions,
        },
        status="sent",
        reply_to_message_id=payload.get("reply_to_message_id") or payload.get("quotedMessageId"),
        extra={
            "thinking_enabled": bool(payload.get("thinking_enabled")),
            "scheduling_strategy": scheduling_strategy,
            "model_config_id": payload.get("model_config_id"),
        },
    )
    conversation.last_message_preview = text[:300]
    conversation.last_message_sender = user.display_name
    conversation.last_message_at = utcnow()
    conversation.activity_score = min(100, conversation.activity_score + 5)
    conversation.message_count += 1
    db.add(message)
    db.commit()
    db.refresh(message)
    if trigger_agent:
        raise ValidationAppError("sync _send cannot trigger agents")
    return message


@router.get("/conversations/{conversation_id}/messages", response_model=ApiResponse[dict])
async def list_messages(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ok(
        {
            "items": await _list_messages(db, user, conversation_id),
            "next_cursor": None,
            "has_more": False,
        }
    )


@router.post("/conversations/{conversation_id}/messages", response_model=ApiResponse[dict])
async def create_message(
    conversation_id: str,
    payload: SendMessagePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    message = await _send(
        db,
        user,
        conversation_id,
        payload.model_dump(),
        trigger_agent=False,
    )
    return ok(message_to_dict(message), "Message saved")


@router.post("/conversations/{conversation_id}/stream")
async def stream_conversation(
    conversation_id: str,
    payload: SendMessagePayload,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Deprecated SSE streaming endpoint."""



    await _get_conversation(db, user, conversation_id)

    channel = f"conversation:{conversation_id}"

    # Handle regenerate requests through the same persisted user-message path.
    message_payload: dict = {}

    if payload.regenerate_message_id:
        original = db.get(Message, payload.regenerate_message_id)
        original_text = (
            original.content.get("text", "")
            if original and isinstance(original.content, dict)
            else ""
        )
        prompt = f"Please regenerate based on the original message:\n{original_text}" if original_text else ""
        message_payload = {
            "client_message_id": str(uuid.uuid4()),
            "content": {"text": prompt},
        }
    else:
        message_payload = payload.model_dump()

    # Save first, then start generation with the persisted message id.
    sse_sink = SseSink(conversation_id)
    message = await _send(db, user, conversation_id, message_payload, trigger_agent=False)

    # Publish the user message event for legacy SSE consumers.
    await event_bus.publish(channel, "message:new", message_to_dict(message))

    # Bridge runtime events into the SSE response stream.
    async def generator():
        from app.services.conversation_session_manager import ConversationRunManager

        session_manager = ConversationRunManager.get_instance()
        conversation = await _get_conversation(db, user, conversation_id)
        await session_manager.get_or_create_engine(
            db,
            conversation,
            model_config_id=message_payload.get("model_config_id"),
            event_sink=sse_sink,
        )
        await session_manager.start_generation(
            conversation_id,
            message.content.get("text", "") if isinstance(message.content, dict) else str(message.content),
            runtime_content=runtime_prompt_for_message(message),
            thinking_enabled=bool(message_payload.get("thinking_enabled")),
            user_message_id=str(message.id),
            client_message_id=message.client_message_id,
            agent_mentions=agent_mentions_for_message(message),
        )

        queue = sse_sink.get_queue()
        if queue is None:
            return

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=0.5)
                await asyncio.sleep(0.05)
                yield {
                    "event": event.type,
                    "data": json.dumps(event.payload, ensure_ascii=False),
                }
                if event.type in ("system.session_completed", "system.session_error"):
                    break
            except asyncio.TimeoutError:
                pass
            except asyncio.CancelledError:
                break

    return EventSourceResponse(generator())


@router.post("/conversations/{conversation_id}/stream/cancel", response_model=ApiResponse[dict])
async def cancel_stream(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel the active generation."""
    from app.services.conversation_session_manager import ConversationRunManager

    conversation = await _get_conversation(db, user, conversation_id)

    # Cancel the legacy SSE orchestration task if it is still running.
    task = ORCHESTRATION_TASKS.get(conversation.id)
    cancelled = False
    if task and not task.done():
        task.cancel()
        cancelled = True

    # Cancel the shared conversation session generation as well.
    session_manager = ConversationRunManager.get_instance()
    if session_manager.is_generation_running(conversation.id):
        await session_manager.cancel_generation(conversation.id)
        cancelled = True

    await event_bus.publish(
        f"conversation:{conversation.id}",
        "generation:cancelled",
        {"conversation_id": conversation.id, "cancelled": cancelled},
    )
    return ok({"conversation_id": conversation.id, "cancelled": cancelled}, "Generation cancelled")


@router.post(
    "/conversations/{conversation_id}/messages/{message_id}/code-runs",
    response_model=ApiResponse[dict],
)
async def run_message_code(
    conversation_id: str,
    message_id: str,
    payload: RunMessageCodeRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if payload.conversation_id and payload.conversation_id != conversation_id:
        raise ValidationAppError("conversation_id does not match request path")
    if payload.message_id and payload.message_id != message_id:
        raise ValidationAppError("message_id does not match request path")
    result = await db.run_sync(
        lambda session: run_message_code_block(
            session,
            user=user,
            conversation_id=conversation_id,
            message_id=message_id,
            language=payload.language,
            code=payload.code,
            index=payload.index,
            timeout_seconds=payload.timeout_seconds,
            workspace_id=payload.workspace_id,
        )
    )
    return ok(result, "Code executed in conversation sandbox")


# ---- compat routes ----


@compat_router.get("/conversations/{conversation_id}/messages", response_model=list[dict])
async def compat_list_messages(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return await _list_messages(db, user, conversation_id)


@compat_router.post("/conversations/{conversation_id}/stream/cancel", response_model=dict)
async def compat_cancel_stream(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Cancel the active generation for compatibility routes."""
    from app.services.conversation_session_manager import ConversationRunManager

    conversation = await _get_conversation(db, user, conversation_id)

    task = ORCHESTRATION_TASKS.get(conversation.id)
    cancelled = False
    if task and not task.done():
        task.cancel()
        cancelled = True

    session_manager = ConversationRunManager.get_instance()
    if session_manager.is_generation_running(conversation.id):
        await session_manager.cancel_generation(conversation.id)
        cancelled = True

    await event_bus.publish(
        f"conversation:{conversation.id}",
        "generation:cancelled",
        {"conversation_id": conversation.id, "cancelled": cancelled},
    )
    return {"conversation_id": conversation.id, "cancelled": cancelled}
