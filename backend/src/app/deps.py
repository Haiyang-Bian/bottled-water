from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import UnauthorizedError
from app.core.security import decode_access_token
from app.services.desktop_identity import get_desktop_user, validate_desktop_session
from db import get_db
from db.models import User


async def get_current_user(
    db: Annotated[AsyncSession, Depends(get_db)],
    authorization: Annotated[str | None, Header()] = None,
    token_query: Annotated[str | None, Query(alias="token")] = None,
    desktop_session: Annotated[
        str | None,
        Header(alias="X-AgentHub-Desktop-Session"),
    ] = None,
    desktop_session_query: Annotated[
        str | None,
        Query(alias="desktop_session"),
    ] = None,
    settings: Settings = Depends(get_settings),
) -> User:
    if settings.desktop_single_user:
        validate_desktop_session(settings, desktop_session or desktop_session_query)
        return await get_desktop_user(db)

    token = token_query
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        raise UnauthorizedError()
    payload = decode_access_token(token)
    if not payload or not payload.get("sub"):
        raise UnauthorizedError("Token 无效或已过期")
    user = await db.get(User, payload["sub"])
    if not user or user.deleted_at is not None or user.status != "active":
        raise UnauthorizedError("用户不存在或已停用")
    return user

