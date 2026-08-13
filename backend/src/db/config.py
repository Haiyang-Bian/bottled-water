from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


ROOT_DIR = Path(__file__).resolve().parents[3]


class DBSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(ROOT_DIR / ".env", ROOT_DIR / "backend" / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+psycopg://agenthub:agenthub@localhost:54326/agenthub"

    @model_validator(mode="after")
    def validate_production_database(self) -> "DBSettings":
        if self.environment == "production":
            password = make_url(self.database_url).password
            normalized_password = (password or "").strip().lower()
            if normalized_password in {
                "",
                "agenthub",
                "agenthub_secret",
                "replace-with-a-strong-database-password",
            } or normalized_password.startswith(("change-me", "replace-with")):
                raise ValueError("DATABASE_URL must use a non-placeholder password in production")
        return self

    @property
    def resolved_database_url(self) -> str:
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace("postgresql://", "postgresql+psycopg://")
        if not self.database_url.startswith("sqlite:///"):
            return self.database_url
        raw_path = self.database_url.removeprefix("sqlite:///")
        if raw_path in {"", ":memory:"}:
            return self.database_url
        if Path(raw_path).is_absolute():
            return self.database_url
        normalized = raw_path.replace("\\", "/")
        base_dir = ROOT_DIR if normalized.startswith(("backend/", "./backend/")) else ROOT_DIR / "backend"
        target = (base_dir / raw_path).resolve()
        return f"sqlite:///{target.as_posix()}"


@lru_cache
def get_db_settings() -> DBSettings:
    return DBSettings()
