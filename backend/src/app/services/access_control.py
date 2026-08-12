from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError
from db.models import Permission, Role, RolePermission, User, UserRole


async def roles_for_user(db: AsyncSession, user: User) -> list[str]:
    rows = await db.scalars(
        select(Role.code)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user.id,
            Role.deleted_at.is_(None),
        )
        .order_by(Role.code)
    )
    return list(rows.all())


async def permissions_for_user(db: AsyncSession, user: User) -> list[str]:
    rows = await db.scalars(
        select(Permission.code)
        .join(RolePermission, RolePermission.permission_id == Permission.id)
        .join(Role, Role.id == RolePermission.role_id)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(
            UserRole.user_id == user.id,
            Role.deleted_at.is_(None),
            Permission.deleted_at.is_(None),
        )
        .distinct()
        .order_by(Permission.code)
    )
    return list(rows.all())


async def has_permission(db: AsyncSession, user: User, permission: str) -> bool:
    return permission in await permissions_for_user(db, user)


async def require_permission(db: AsyncSession, user: User, permission: str) -> None:
    if not await has_permission(db, user, permission):
        raise ForbiddenError(f"缺少权限：{permission}")
