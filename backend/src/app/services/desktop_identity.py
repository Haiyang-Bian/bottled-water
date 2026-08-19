from __future__ import annotations

import secrets
from hmac import compare_digest

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.security import hash_password
from db.models import Role, User, UserRole, UserSettings


DESKTOP_USER_EMAIL = "local@agenthub.desktop"
DESKTOP_USER_NAME = "local-user"
DESKTOP_USER_MARKER = "desktop_local_user"
DESKTOP_ROLE_CODES = (
    "ROLE_USER",
    "ROLE_AGENT_PROVIDER",
    "ROLE_DEVELOPER",
    "ROLE_ADMIN",
)


def require_account_auth_enabled(settings: Settings) -> None:
    if settings.desktop_single_user:
        raise NotFoundError("Account authentication is unavailable in desktop single-user mode")


def require_user_management_enabled(settings: Settings) -> None:
    if settings.desktop_single_user:
        raise NotFoundError("User management is unavailable in desktop single-user mode")


def validate_desktop_session(settings: Settings, token: str | None) -> None:
    expected = settings.desktop_session_token or ""
    if (
        not settings.desktop_single_user
        or not token
        or not expected
        or not compare_digest(token, expected)
    ):
        raise UnauthorizedError("Desktop session is invalid or expired")


def _is_managed_desktop_user(user: User) -> bool:
    return bool((user.extra or {}).get(DESKTOP_USER_MARKER))


async def get_desktop_user(db: AsyncSession) -> User:
    users = (
        await db.scalars(
            select(User).where(
                User.deleted_at.is_(None),
                User.status == "active",
            )
        )
    ).all()
    user = next((item for item in users if _is_managed_desktop_user(item)), None)
    if not user:
        raise UnauthorizedError("Desktop identity is not initialized")
    return user


async def ensure_desktop_user(db: AsyncSession, settings: Settings) -> User | None:
    """Create or adopt the sole local identity used by the packaged desktop app."""
    if not settings.desktop_single_user:
        return None

    users = (
        await db.scalars(
            select(User)
            .where(User.deleted_at.is_(None), User.status == "active")
            .order_by(User.created_at, User.id)
        )
    ).all()
    user = next((item for item in users if _is_managed_desktop_user(item)), None)
    if not user:
        user = next(
            (
                item
                for item in users
                if item.email == DESKTOP_USER_EMAIL or item.username == DESKTOP_USER_NAME
            ),
            None,
        )
    if not user and len(users) == 1:
        # Preserve conversations and provider credentials created by the first
        # desktop release, which still required account registration.
        user = users[0]
    if not user:
        user = User(
            email=DESKTOP_USER_EMAIL,
            username=DESKTOP_USER_NAME,
            password_hash=hash_password(secrets.token_urlsafe(48)),
            display_name="本机用户",
            role="admin",
            status="active",
            extra={},
        )
        db.add(user)
        await db.flush()

    user.role = "admin"
    user.extra = {
        **(user.extra or {}),
        DESKTOP_USER_MARKER: True,
        "system_managed": True,
    }
    user_settings = await db.scalar(select(UserSettings).where(UserSettings.user_id == user.id))
    if not user_settings:
        db.add(UserSettings(user_id=user.id, theme="light"))

    roles = (
        await db.scalars(
            select(Role).where(
                Role.code.in_(DESKTOP_ROLE_CODES),
                Role.deleted_at.is_(None),
            )
        )
    ).all()
    roles_by_code = {role.code: role for role in roles}
    missing = [code for code in DESKTOP_ROLE_CODES if code not in roles_by_code]
    if missing:
        raise RuntimeError(f"Desktop roles are missing: {', '.join(missing)}")

    existing_roles = {
        row.role_id
        for row in (
            await db.scalars(select(UserRole).where(UserRole.user_id == user.id))
        ).all()
    }
    for code in DESKTOP_ROLE_CODES:
        role = roles_by_code[code]
        if role.id not in existing_roles:
            db.add(UserRole(user_id=user.id, role_id=role.id, assigned_by=user.id))

    await db.commit()
    await db.refresh(user)
    return user
