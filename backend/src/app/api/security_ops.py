from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ForbiddenError, ValidationAppError
from app.core.response import ok
from app.deps import get_current_user
from db import get_db
from db.models import AuditLog, Permission, Role, RolePermission, User, UserRole, utcnow
from app.schemas.common import ApiResponse
from app.services.access_control import permissions_for_user, require_permission, roles_for_user
from app.services.audit import write_audit_log
from app.services.serialization import iso


router = APIRouter(tags=["security-ops"])


async def _role_to_dict(db: AsyncSession, role: Role) -> dict:
    permission_ids = {
        item.permission_id
        for item in (
            await db.scalars(select(RolePermission).where(RolePermission.role_id == role.id))
        ).all()
    }
    permissions = (
        (await db.scalars(select(Permission).where(Permission.id.in_(permission_ids)))).all()
        if permission_ids
        else []
    )
    return {
        "id": role.id,
        "code": role.code,
        "name": role.name,
        "description": role.description,
        "is_system": role.is_system,
        "permissions": [
            {
                "id": permission.id,
                "code": permission.code,
                "resource": permission.resource,
                "action": permission.action,
                "description": permission.description,
            }
            for permission in permissions
        ],
        "created_at": iso(role.created_at),
        "updated_at": iso(role.updated_at),
    }


def _permission_to_dict(permission: Permission) -> dict:
    return {
        "id": permission.id,
        "code": permission.code,
        "resource": permission.resource,
        "action": permission.action,
        "description": permission.description,
        "created_at": iso(permission.created_at),
        "updated_at": iso(permission.updated_at),
    }


def _role_code_for_user_role(value: str) -> tuple[str, str]:
    raw = str(value or "member").strip()
    if not raw:
        raw = "member"
    code = raw.upper() if raw.upper().startswith("ROLE_") else f"ROLE_{raw.upper()}"
    if code == "ROLE_MEMBER":
        code = "ROLE_USER"
    role_value = code.removeprefix("ROLE_").lower()
    if role_value == "user":
        role_value = "member"
    return code, role_value


async def _roles_for_user_value(db: AsyncSession, role_value: str) -> tuple[str, list[Role]]:
    code, normalized_role = _role_code_for_user_role(role_value)
    role_codes = ["ROLE_USER"] if code == "ROLE_USER" else ["ROLE_USER", code]
    roles = (
        await db.scalars(
            select(Role).where(Role.code.in_(role_codes), Role.deleted_at.is_(None))
        )
    ).all()
    by_code = {role.code: role for role in roles}
    missing = [role_code for role_code in role_codes if role_code not in by_code]
    if missing:
        raise ValidationAppError(f"Role not found: {', '.join(missing)}")
    return normalized_role, [by_code[item] for item in role_codes]


@router.get("/permissions/me", response_model=ApiResponse[dict])
async def my_permissions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    permissions = await permissions_for_user(db, user)
    roles = await roles_for_user(db, user)
    return ok(
        {
            "user_id": user.id,
            "role": user.role,
            "roles": roles,
            "permissions": permissions,
        }
    )


@router.get("/security/permissions", response_model=ApiResponse[dict])
async def list_permissions(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "security:view")
    permissions = (
        await db.scalars(select(Permission).order_by(Permission.resource, Permission.action))
    ).all()
    return ok(
        {"items": [_permission_to_dict(item) for item in permissions], "total": len(permissions)}
    )


@router.get("/security/roles", response_model=ApiResponse[dict])
async def list_roles(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "security:view")
    roles = (
        await db.scalars(select(Role).where(Role.deleted_at.is_(None)).order_by(Role.code))
    ).all()
    return ok({"items": [await _role_to_dict(db, item) for item in roles], "total": len(roles)})


class CreateRoleRequest(BaseModel):
    code: str | None = None
    name: str | None = None
    description: str = ""


@router.post("/security/roles", response_model=ApiResponse[dict])
async def create_role(
    payload: CreateRoleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "security:manage")
    code = str(payload.code or "").strip().upper()
    if not code.startswith("ROLE_"):
        code = f"ROLE_{code or 'CUSTOM'}"
    role = Role(
        code=code,
        name=str(payload.name or code),
        description=str(payload.description or ""),
        is_system=False,
    )
    db.add(role)
    await db.flush()
    await write_audit_log(
        db,
        user=user,
        action="security.role.create",
        target_type="role",
        target_id=role.id,
        detail={"code": code},
        risk_score=0.3,
    )
    await db.commit()
    await db.refresh(role)
    return ok(await _role_to_dict(db, role), "Role created")


class UpdateRolePermissionsRequest(BaseModel):
    permission_codes: list[str] = Field(default_factory=list)


@router.patch("/security/roles/{role_id}/permissions", response_model=ApiResponse[dict])
async def update_role_permissions(
    role_id: str,
    payload: UpdateRolePermissionsRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "security:manage")
    role = await db.get(Role, role_id)
    if not role or role.deleted_at is not None:
        raise ForbiddenError("Role not found")
    if role.is_system:
        raise ForbiddenError("System role permissions are immutable")
    codes = [str(item) for item in payload.permission_codes]
    permissions = (
        (await db.scalars(select(Permission).where(Permission.code.in_(codes)))).all()
        if codes
        else []
    )
    found_codes = {item.code for item in permissions}
    missing_codes = sorted(set(codes) - found_codes)
    if missing_codes:
        raise ValidationAppError(f"Permission not found: {', '.join(missing_codes)}")
    for row in (
        await db.scalars(select(RolePermission).where(RolePermission.role_id == role.id))
    ).all():
        await db.delete(row)
    await db.flush()
    for permission in permissions:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await write_audit_log(
        db,
        user=user,
        action="security.role.permissions.update",
        target_type="role",
        target_id=role.id,
        detail={"permission_codes": codes},
        risk_score=0.45,
    )
    await db.commit()
    return ok(await _role_to_dict(db, role), "Role permissions updated")


@router.get("/security/users", response_model=ApiResponse[dict])
async def list_security_users(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "user:manage")
    users = (
        await db.scalars(
            select(User)
            .where(User.deleted_at.is_(None))
            .order_by(User.created_at.desc())
            .limit(200)
        )
    ).all()
    role_rows = (await db.scalars(select(UserRole))).all()
    by_user: dict[str, list[str]] = {}
    role_map = {role.id: role.code for role in (await db.scalars(select(Role))).all()}
    for row in role_rows:
        by_user.setdefault(row.user_id, []).append(role_map.get(row.role_id, row.role_id))
    return ok(
        {
            "items": [
                {
                    "id": item.id,
                    "email": item.email,
                    "username": item.username,
                    "display_name": item.display_name,
                    "role": item.role,
                    "status": item.status,
                    "roles": by_user.get(item.id, []),
                    "last_login_at": iso(item.last_login_at),
                    "created_at": iso(item.created_at),
                }
                for item in users
            ],
            "total": len(users),
        }
    )


class UpdateUserRoleRequest(BaseModel):
    role: str = "member"


@router.patch("/security/users/{target_user_id}/role", response_model=ApiResponse[dict])
async def update_user_role(
    target_user_id: str,
    payload: UpdateUserRoleRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "user:manage")
    target = await db.get(User, target_user_id)
    if not target or target.deleted_at is not None:
        raise ForbiddenError("User not found")
    previous_role = target.role
    normalized_role, roles = await _roles_for_user_value(db, payload.role)
    if target.id == user.id and normalized_role != "admin":
        raise ForbiddenError("Administrators cannot demote themselves")
    if previous_role == "admin" and normalized_role != "admin":
        active_admin_count = await db.scalar(
            select(func.count(User.id))
            .join(UserRole, UserRole.user_id == User.id)
            .join(Role, Role.id == UserRole.role_id)
            .where(
                Role.code == "ROLE_ADMIN",
                User.status == "active",
                User.deleted_at.is_(None),
            )
        )
        if int(active_admin_count or 0) <= 1:
            raise ForbiddenError("Cannot demote the last active administrator")
    target.role = normalized_role
    target.updated_at = utcnow()
    for row in (
        await db.scalars(select(UserRole).where(UserRole.user_id == target.id))
    ).all():
        await db.delete(row)
    await db.flush()
    for role in roles:
        db.add(UserRole(user_id=target.id, role_id=role.id, assigned_by=user.id))
    await write_audit_log(
        db,
        user=user,
        action="security.user.role.update",
        target_type="user",
        target_id=target.id,
        detail={
            "previous_role": previous_role,
            "role": normalized_role,
            "role_codes": [role.code for role in roles],
        },
        risk_score=0.55,
    )
    await db.commit()
    return ok(
        {
            "id": target.id,
            "role": target.role,
            "roles": [role.code for role in roles],
        },
        "User role updated",
    )


@router.get("/audit-logs", response_model=ApiResponse[dict])
async def list_audit_logs(
    target_type: str | None = None,
    action: str | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    can_view_all = "log:view:all" in await permissions_for_user(db, user)
    if not can_view_all:
        query = select(AuditLog).where(AuditLog.actor_id == user.id)
    else:
        query = select(AuditLog)
    if target_type:
        query = query.where(AuditLog.target_type == target_type)
    if action:
        query = query.where(AuditLog.action == action)
    total = len((await db.scalars(query)).all())
    logs = (
        await db.scalars(
            query.order_by(AuditLog.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return ok(
        {
            "items": [
                {
                    "id": item.id,
                    "actor_id": item.actor_id,
                    "action": item.action,
                    "target_type": item.target_type,
                    "target_id": item.target_id,
                    "ip_address": item.ip_address,
                    "risk_score": item.risk_score,
                    "detail": item.detail,
                    "created_at": iso(item.created_at),
                }
                for item in logs
            ],
            "total": total,
            "page": page,
            "page_size": page_size,
        }
    )


@router.get("/audit-logs/stats", response_model=ApiResponse[dict])
async def audit_stats(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    can_view_all = "log:view:all" in await permissions_for_user(db, user)
    if not can_view_all:
        query = select(AuditLog).where(AuditLog.actor_id == user.id)
    else:
        query = select(AuditLog)
    logs = (await db.scalars(query)).all()
    by_action: dict[str, int] = {}
    high_risk = 0
    for log in logs:
        by_action[log.action] = by_action.get(log.action, 0) + 1
        if (log.risk_score or 0) >= 0.5:
            high_risk += 1
    return ok(
        {
            "total": len(logs),
            "high_risk": high_risk,
            "by_action": by_action,
            "latest_at": iso(max((item.created_at for item in logs), default=None)),
        }
    )


@router.get("/admin/guard", response_model=ApiResponse[dict])
async def admin_guard(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    await require_permission(db, user, "user:manage")
    return ok({"allowed": True})
