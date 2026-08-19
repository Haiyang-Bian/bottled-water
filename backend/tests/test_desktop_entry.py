from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from desktop_entry import parse_args, prepare_desktop_environment
from app.core.config import Settings
from app.core.errors import NotFoundError, UnauthorizedError
from app.services.desktop_identity import (
    DESKTOP_ROLE_CODES,
    ensure_desktop_user,
    require_account_auth_enabled,
    require_user_management_enabled,
    validate_desktop_session,
)
from db import Base
from db.models import Role, User, UserRole, UserSettings


pytestmark = [pytest.mark.desktop, pytest.mark.unit]
SESSION_TOKEN = "a" * 64


def desktop_settings() -> Settings:
    return Settings(
        environment="desktop",
        debug=False,
        secret_key="b" * 64,
        desktop_single_user=True,
        desktop_session_token=SESSION_TOKEN,
        cors_origins=[],
        cors_origin_regex=None,
    )


def test_desktop_environment_uses_stable_secrets_and_local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    with patch.dict(os.environ, {}, clear=False):
        secrets_path = prepare_desktop_environment(tmp_path, 18765, SESSION_TOKEN)
        first = json.loads(secrets_path.read_text(encoding="utf-8"))
        prepare_desktop_environment(tmp_path, 18766, SESSION_TOKEN)
        second = json.loads(secrets_path.read_text(encoding="utf-8"))

        assert first == second
        assert len(first["secret_key"]) == 64
        assert len(first["data_encryption_key"]) == 64
        assert first["secret_key"] not in str(secrets_path)
        assert os.environ["ENVIRONMENT"] == "desktop"
        assert os.environ["DATABASE_URL"].endswith("/agenthub.db")
        assert os.environ["ARTIFACT_BASE_URL"] == "http://127.0.0.1:18766"
        assert os.environ["DESKTOP_SINGLE_USER"] == "true"
        assert os.environ["DESKTOP_SESSION_TOKEN"] == SESSION_TOKEN


def test_desktop_entry_requires_explicit_data_dir_port_and_session():
    args = parse_args(
        [
            "--data-dir",
            "desktop-data",
            "--port",
            "18000",
            "--session-token",
            SESSION_TOKEN,
        ]
    )

    assert args.data_dir.name == "desktop-data"
    assert args.port == 18000
    assert args.session_token == SESSION_TOKEN


def test_desktop_environment_does_not_allow_browser_origin_regex():
    settings = Settings(
        environment="desktop",
        debug=False,
        secret_key="a" * 64,
        cors_origins=[],
        cors_origin_regex=None,
    )

    assert settings.cors_origin_regex is None
    assert "https://tauri.localhost" in settings.cors_origins


def test_desktop_session_and_account_management_contract():
    settings = desktop_settings()

    validate_desktop_session(settings, SESSION_TOKEN)
    with pytest.raises(UnauthorizedError):
        validate_desktop_session(settings, "wrong")
    with pytest.raises(NotFoundError):
        require_account_auth_enabled(settings)
    with pytest.raises(NotFoundError):
        require_user_management_enabled(settings)


@pytest.mark.asyncio
async def test_desktop_identity_adopts_existing_user_and_assigns_local_roles():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    tables = [
        User.__table__,
        UserSettings.__table__,
        Role.__table__,
        UserRole.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as db:
        db.add_all([Role(code=code, name=code, is_system=True) for code in DESKTOP_ROLE_CODES])
        existing = User(
            email="existing@example.com",
            username="existing",
            password_hash="unused",
            display_name="Existing User",
        )
        db.add(existing)
        await db.commit()

        local_user = await ensure_desktop_user(db, desktop_settings())
        assert local_user is not None
        assert local_user.id == existing.id
        assert local_user.role == "admin"
        assert local_user.extra["desktop_local_user"] is True

        assigned_codes = set(
            (
                await db.scalars(
                    select(Role.code)
                    .join(UserRole, UserRole.role_id == Role.id)
                    .where(UserRole.user_id == local_user.id)
                )
            ).all()
        )
        assert assigned_codes == set(DESKTOP_ROLE_CODES)

        second = await ensure_desktop_user(db, desktop_settings())
        assert second is not None
        assert second.id == local_user.id

    await engine.dispose()
