"""WebSocket conversation endpoint."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.core.types import Event as RuntimeEvent
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.security import decode_access_token
from app.events import WebSocketSink
from app.services.chat.message_prompt import agent_mentions_for_message, runtime_prompt_for_message
from app.services.chat.user_messages import message_text, save_user_message
from app.services.conversation_session_manager import ConversationRunManager
from common.logger import get_logger
from db.models import Conversation, Message, User
from db.session import AsyncSessionLocal

logger = get_logger("app.api.websocket")

router = APIRouter(tags=["websocket"])


async def _authenticate_ws(token: str) -> User:
    """Authenticate a WebSocket token."""
    if not token:
        raise UnauthorizedError()
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise UnauthorizedError("Token is invalid or expired")

    async with AsyncSessionLocal() as db:
        user = await db.get(User, payload["sub"])
        if not user or user.deleted_at is not None:
            raise UnauthorizedError("User not found or disabled")
        return user


async def _get_conversation_ws(db: AsyncSession, user: User, conversation_id: str) -> Conversation:
    """Return the requested conversation for a WebSocket user."""
    conversation = await db.scalar(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.creator_id == user.id,
            Conversation.deleted_at.is_(None),
        )
    )
    if not conversation:
        raise NotFoundError("Conversation not found")
    return conversation


def _message_text(payload: dict) -> str:
    return message_text(payload)


async def _save_user_message(
    db: AsyncSession,
    user: User,
    conversation: Conversation,
    data: dict,
) -> Message:
    return await save_user_message(db, user=user, conversation=conversation, payload=data)


@router.websocket("/ws/conversations/{conversation_id}")
async def conversation_websocket(
    websocket: WebSocket,
    conversation_id: str,
    token: str = Query(...),
) -> None:
    """Conversation-level WebSocket endpoint."""
    try:
        user = await _authenticate_ws(token)
    except UnauthorizedError as exc:
        await websocket.close(code=4001, reason=str(exc))
        return

    await websocket.accept()
    logger.info("WebSocket connected", conversation_id=conversation_id, user_id=user.id)

    async with AsyncSessionLocal() as db:
        try:
            await _get_conversation_ws(db, user, conversation_id)
        except Exception as exc:
            await websocket.close(code=4004, reason=str(exc))
            return

    sink = WebSocketSink(conversation_id)
    sink.register(websocket)
    session_manager = ConversationRunManager.get_instance()
    try:
        await session_manager.recover_conversation(conversation_id, reason="process_lost")
    except Exception as exc:
        logger.warning(
            "WebSocket recovery check failed",
            conversation_id=conversation_id,
            error=str(exc),
        )

    try:
        while True:
            msg = await websocket.receive_text()
            try:
                payload = json.loads(msg)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "data": {"message": "Invalid JSON"}})
                continue

            event_type = payload.get("event")
            data = payload.get("data") or {}
            request_id = payload.get("request_id")

            if event_type == "chat.send":
                await _handle_chat_send(
                    websocket,
                    conversation_id,
                    user,
                    data,
                    request_id,
                    sink,
                    session_manager,
                )
            elif event_type == "chat.cancel":
                await _handle_chat_cancel(websocket, conversation_id, request_id, session_manager)
            elif event_type == "ping":
                await websocket.send_json({"event": "pong", "data": {}})
            else:
                await websocket.send_json(
                    {
                        "event": "error",
                        "data": {"message": f"Unsupported event type: {event_type}"},
                        "request_id": request_id,
                    }
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected", conversation_id=conversation_id, user_id=user.id)
    except Exception as exc:
        logger.error(
            f"WebSocket failed: {type(exc).__name__}: {exc}",
            conversation_id=conversation_id,
            exc_info=True,
        )
    finally:
        sink.unregister(websocket)


async def _handle_chat_send(
    websocket: WebSocket,
    conversation_id: str,
    user: User,
    data: dict,
    request_id: str | None,
    sink: WebSocketSink,
    session_manager: ConversationRunManager,
) -> None:
    """Handle chat.send."""
    async with AsyncSessionLocal() as db:
        try:
            conversation = await _get_conversation_ws(db, user, conversation_id)
            message = await _save_user_message(db, user, conversation, data)

            await session_manager.get_or_create_engine(
                db,
                conversation,
                model_config_id=data.get("model_config_id"),
                event_sink=sink,
            )

            content = (
                message.content.get("text", "")
                if isinstance(message.content, dict)
                else str(message.content)
            )
            runtime_content = runtime_prompt_for_message(message)
            agent_mentions = agent_mentions_for_message(message)
            asyncio.create_task(
                _send_user_input_async(
                    session_manager,
                    conversation_id,
                    content,
                    runtime_content,
                    bool(data.get("thinking_enabled")),
                    str(message.id),
                    message.client_message_id,
                    agent_mentions,
                ),
                name=f"ws-send-{conversation_id}",
            )

            await websocket.send_json(
                {
                    "event": "chat.ack",
                    "data": {"message_id": str(message.id), "content_preview": content[:100]},
                    "request_id": request_id,
                }
            )

        except Exception as exc:
            logger.error(
                f"chat.send failed: {type(exc).__name__}: {exc}",
                conversation_id=conversation_id,
                request_id=request_id or "",
                exc_info=True,
            )
            failure_payload = {
                "conversation_id": conversation_id,
                "status": "failed",
                "error": str(exc),
                "error_type": type(exc).__name__,
            }
            await websocket.send_json(
                {
                    "event": "generation:failed",
                    "data": failure_payload,
                    "request_id": request_id,
                }
            )
            await websocket.send_json(
                {
                    "event": "error",
                    "data": {"message": str(exc), **failure_payload},
                    "request_id": request_id,
                }
            )


async def _send_user_input_async(
    session_manager: ConversationRunManager,
    conversation_id: str,
    content: str,
    runtime_content: str | None = None,
    thinking_enabled: bool = False,
    user_message_id: str | None = None,
    client_message_id: str | None = None,
    agent_mentions: list[dict[str, str]] | None = None,
) -> None:
    """Send user input to the session in the background."""
    try:
        await session_manager.send_user_input(
            conversation_id,
            content,
            runtime_content=runtime_content,
            thinking_enabled=thinking_enabled,
            user_message_id=user_message_id,
            client_message_id=client_message_id,
            agent_mentions=agent_mentions,
        )
    except Exception as exc:
        await WebSocketSink(conversation_id).emit(
            RuntimeEvent(
                type="generation:failed",
                payload={
                    "conversation_id": conversation_id,
                    "status": "failed",
                    "error": str(exc),
                },
            )
        )
        logger.error("Sending user input failed", conversation_id=conversation_id, error=str(exc))


async def _handle_chat_cancel(
    websocket: WebSocket,
    conversation_id: str,
    request_id: str | None,
    session_manager: ConversationRunManager,
) -> None:
    """Handle chat.cancel."""
    try:
        cancelled = await session_manager.cancel_generation(conversation_id)
        await websocket.send_json(
            {
                "event": "chat.cancelled",
                "data": {"cancelled": cancelled},
                "request_id": request_id,
            }
        )
    except Exception as exc:
        logger.error("chat.cancel failed", conversation_id=conversation_id, error=str(exc))
        await websocket.send_json(
            {
                "event": "error",
                "data": {"message": str(exc)},
                "request_id": request_id,
            }
        )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """Global WebSocket endpoint kept for protocol compatibility."""
    await websocket.accept()
    subscribed: set[str] = set()
    try:
        while True:
            message = await websocket.receive_text()
            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                await websocket.send_json({"event": "error", "data": {"message": "Invalid JSON"}})
                continue
            event = payload.get("event")
            data = payload.get("data") or {}
            if event == "ping":
                await websocket.send_json({"event": "pong", "data": {}})
            elif event == "subscribe":
                channel = data.get("channel")
                if channel and channel not in subscribed:
                    subscribed.add(channel)
                    await websocket.send_json({"event": "subscribed", "data": {"channel": channel}})
            else:
                await websocket.send_json({"event": "ack", "data": {"event": event}})
    except WebSocketDisconnect:
        return
