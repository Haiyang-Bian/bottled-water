from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, UnauthorizedError, ValidationAppError
from app.core.response import ok
from app.core.security import create_access_token, hash_password, verify_password
from app.deps import get_current_user
from db import get_db
from db.models import Role, User, UserRole, UserSettings, utcnow
from app.schemas.common import ApiResponse, LoginOut, OkResponse, UserResponse
from app.schemas.requests import (
    ChangePasswordRequest,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
)
from app.services.serialization import user_to_dict


router = APIRouter(tags=["auth"])
compat_router = APIRouter(tags=["auth-compat"])


async def _find_user(db: AsyncSession, username_or_email: str) -> User | None:
    return await db.scalar(
        select(User).where(
            or_(User.email == username_or_email, User.username == username_or_email),
            User.deleted_at.is_(None),
        )
    )


def _login_response(user: User) -> dict:
    token = create_access_token(user.id, {"email": user.email, "role": user.role})
    return {"access_token": token, "token": token, "user": user_to_dict(user)}


async def _register(db: AsyncSession, payload: dict) -> tuple[dict, int]:
    email = str(payload.get("email") or "").strip().lower()
    username = payload.get("username") or payload.get("name") or email.split("@")[0]
    username = str(username or "").strip()
    password = str(payload.get("password") or "")
    if not email or not username or not password:
        raise ValidationAppError("邮箱、用户名和密码不能为空")
    existing = await _find_user(db, email) or await _find_user(db, username)
    if existing:
        raise ConflictError("邮箱或用户名已存在")
    user = User(
        email=email,
        username=username,
        password_hash=hash_password(password),
        display_name=payload.get("display_name") or payload.get("name") or username,
    )
    db.add(user)
    try:
        await db.flush()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("邮箱或用户名已存在") from exc
    db.add(UserSettings(user_id=user.id, theme="light"))
    member_role = await db.scalar(
        select(Role).where(Role.code == "ROLE_USER", Role.deleted_at.is_(None))
    )
    if not member_role:
        raise ValidationAppError("系统角色尚未初始化")
    db.add(UserRole(user_id=user.id, role_id=member_role.id, assigned_by=user.id))
    await db.commit()
    await db.refresh(user)
    return _login_response(user), 201


async def _login(db: AsyncSession, payload: dict) -> dict:
    username = payload.get("username") or payload.get("email") or payload.get("name")
    password = str(payload.get("password") or "")
    if not username or not password:
        raise ValidationAppError("用户名和密码不能为空")
    user = await _find_user(db, username)
    if not user or user.status != "active" or not verify_password(password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误")
    user.last_login_at = utcnow()
    user.login_count += 1
    await db.commit()
    await db.refresh(user)
    return _login_response(user)


@router.post("/auth/register", response_model=ApiResponse[LoginOut])
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册。

    Args:
        payload: 注册请求，包含邮箱、用户名和密码。
        db: 数据库会话。

    Returns:
        包含访问令牌和用户信息的成功响应。
    """
    data, _ = await _register(db, payload.model_dump())
    return ok(data, "注册成功")


@router.post("/auth/signup", response_model=ApiResponse[LoginOut])
async def signup_alias(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    """用户注册（别名端点，与 /auth/register 等价）。

    Args:
        payload: 注册请求，包含邮箱、用户名和密码。
        db: 数据库会话。

    Returns:
        包含访问令牌和用户信息的成功响应。
    """
    data, _ = await _register(db, payload.model_dump())
    return ok(data, "注册成功")


@router.post("/auth/login", response_model=ApiResponse[LoginOut])
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录。

    Args:
        payload: 登录请求，支持邮箱或用户名及密码。
        db: 数据库会话。

    Returns:
        包含访问令牌和用户信息的成功响应。
    """
    return ok(await _login(db, payload.model_dump()), "登录成功")


@router.get("/auth/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    """获取当前登录用户信息。

    Args:
        user: 当前认证用户，由依赖注入解析。

    Returns:
        当前用户的详细信息。
    """
    return ok(user_to_dict(user))


@router.post("/auth/logout", response_model=ApiResponse[OkResponse])
async def logout():
    """用户登出。

    Returns:
        表示登出成功的响应。
    """
    return ok({"ok": True}, "已退出")


@router.patch("/auth/me", response_model=UserResponse)
async def update_me(
    payload: UpdateProfileRequest,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新当前用户资料。

    Args:
        payload: 资料更新请求，支持 display_name、avatar_url 和 settings。
        db: 数据库会话。
        user: 当前认证用户。

    Returns:
        更新后的用户信息。
    """
    raw = payload.model_dump(exclude_unset=True)
    display_name = str(
        raw.get("display_name") or raw.get("name") or user.display_name,
    ).strip()
    if not display_name:
        raise ValidationAppError("display name cannot be empty")
    user.display_name = display_name[:100]
    if "avatar_url" in raw:
        user.avatar_url = str(raw.get("avatar_url") or "") or None
    if "signature" in raw:
        user.extra = {**(user.extra or {}), "signature": str(raw.get("signature") or "").strip()[:120]}
    if isinstance(raw.get("settings"), dict):
        if not user.settings:
            db.add(UserSettings(user_id=user.id, theme="light"))
            await db.flush()
        user.extra = {**(user.extra or {}), "ui_settings": raw["settings"]}
    await db.commit()
    await db.refresh(user)
    return ok(user_to_dict(user), "profile updated")


@router.post("/auth/password", response_model=ApiResponse[OkResponse])
async def change_password(
    payload: ChangePasswordRequest,
        db: AsyncSession = Depends(get_db),
        user: User = Depends(get_current_user),
    ):
    """修改当前用户密码。

    Args:
        payload: 密码修改请求，包含当前密码和新密码。
        db: 数据库会话。
        user: 当前认证用户。

    Returns:
        表示密码修改成功的响应。
    """
    raw = payload.model_dump(exclude_unset=True)
    current_password = str(raw.get("current_password") or raw.get("old_password") or "")
    new_password = str(raw.get("new_password") or raw.get("password") or "")
    if not current_password or not new_password:
        raise ValidationAppError("current password and new password are required")
    if len(new_password) < 6:
        raise ValidationAppError("new password must be at least 6 characters")
    managed_user = await db.get(User, user.id)
    if not managed_user or managed_user.deleted_at is not None:
        raise UnauthorizedError("user not found")
    if not verify_password(current_password, managed_user.password_hash):
        raise UnauthorizedError("current password is incorrect")
    managed_user.password_hash = hash_password(new_password)
    await db.commit()
    return ok({"ok": True, "changed": True}, "password updated")


async def _compat_payload(request: Request) -> dict:
    try:
        return await request.json()
    except Exception:
        return {}


@compat_router.post("/auth/signup")
async def compat_signup(request: Request, db: AsyncSession = Depends(get_db)):
    data, status = await _register(db, await _compat_payload(request))
    return data if status != 409 else data


@compat_router.post("/auth/login")
async def compat_login(request: Request, db: AsyncSession = Depends(get_db)):
    return await _login(db, await _compat_payload(request))


@compat_router.get("/auth/me")
async def compat_me(user: User = Depends(get_current_user)):
    return user_to_dict(user)
