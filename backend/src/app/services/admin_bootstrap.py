from __future__ import annotations

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.security import hash_password
from db.models import Role, User, UserRole


async def active_admin_exists(db: AsyncSession) -> bool:
    admin = await db.scalar(
        select(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .where(
            Role.code == "ROLE_ADMIN",
            User.status == "active",
            User.deleted_at.is_(None),
            Role.deleted_at.is_(None),
        )
        .limit(1)
    )
    return admin is not None


async def create_initial_admin(
    db: AsyncSession,
    *,
    email: str,
    username: str,
    password: str,
) -> User | None:
    if await active_admin_exists(db):
        return None
    email = email.strip().lower()
    username = username.strip()
    if not email or not username or len(password) < 12:
        raise ValueError("Admin email and username are required; password must be at least 12 characters")

    user = await db.scalar(
        select(User).where(or_(User.email == email, User.username == username))
    )
    if not user:
        user = User(
            email=email,
            username=username,
            password_hash=hash_password(password),
            display_name=username,
        )
        db.add(user)
        await db.flush()
    else:
        user.email = email
        user.username = username
        user.password_hash = hash_password(password)
    user.role = "admin"
    user.status = "active"
    user.deleted_at = None

    roles = (
        await db.scalars(select(Role).where(Role.code.in_(["ROLE_USER", "ROLE_ADMIN"])))
    ).all()
    by_code = {role.code: role for role in roles}
    if set(by_code) != {"ROLE_USER", "ROLE_ADMIN"}:
        raise RuntimeError("System roles are not initialized")
    await db.execute(delete(UserRole).where(UserRole.user_id == user.id))
    for code in ("ROLE_USER", "ROLE_ADMIN"):
        db.add(UserRole(user_id=user.id, role_id=by_code[code].id, assigned_by=user.id))
    await db.commit()
    await db.refresh(user)
    return user


async def bootstrap_admin_from_settings(
    db: AsyncSession, settings: Settings | None = None
) -> User | None:
    settings = settings or get_settings()
    values = (
        settings.bootstrap_admin_email,
        settings.bootstrap_admin_username,
        settings.bootstrap_admin_password,
    )
    if all(values):
        return await create_initial_admin(
            db,
            email=values[0] or "",
            username=values[1] or "",
            password=values[2] or "",
        )
    if any(values):
        raise RuntimeError("All AGENTHUB_BOOTSTRAP_ADMIN_* values must be provided together")
    if settings.environment == "production" and not await active_admin_exists(db):
        raise RuntimeError(
            "No administrator exists. Run `python -m app.cli create-admin` or configure "
            "AGENTHUB_BOOTSTRAP_ADMIN_EMAIL/USERNAME/PASSWORD for the first deployment."
        )
    return None
