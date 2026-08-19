"""Packaged desktop entry point for the AgentHub FastAPI sidecar."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
from pathlib import Path


def prepare_desktop_environment(data_dir: Path, port: int, session_token: str) -> Path:
    """Create stable local secrets and configure isolated desktop storage."""
    data_dir = data_dir.expanduser().resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    secrets_path = data_dir / "desktop-secrets.json"
    if secrets_path.exists():
        values = json.loads(secrets_path.read_text(encoding="utf-8"))
    else:
        values = {
            "secret_key": secrets.token_hex(32),
            "data_encryption_key": secrets.token_hex(32),
            "data_encryption_key_id": secrets.token_hex(6),
        }
        temporary = secrets_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(values, indent=2), encoding="utf-8")
        temporary.replace(secrets_path)

    required = {"secret_key", "data_encryption_key", "data_encryption_key_id"}
    if not required <= values.keys():
        raise RuntimeError("desktop secrets file is incomplete")

    storage_dir = data_dir / "storage"
    storage_dir.mkdir(parents=True, exist_ok=True)
    database_path = (data_dir / "agenthub.db").as_posix()
    base_url = f"http://127.0.0.1:{port}"
    if len(session_token) < 32:
        raise ValueError("desktop session token must contain at least 32 characters")
    os.environ.update(
        {
            "AGENTHUB_DESKTOP_MODE": "1",
            "AGENTHUB_DESKTOP_DATA_DIR": str(data_dir),
            "DESKTOP_SINGLE_USER": "true",
            "DESKTOP_SESSION_TOKEN": session_token,
            "ENVIRONMENT": "desktop",
            "DEBUG": "false",
            "SECRET_KEY": str(values["secret_key"]),
            "DATA_ENCRYPTION_KEY": str(values["data_encryption_key"]),
            "DATA_ENCRYPTION_KEY_ID": str(values["data_encryption_key_id"]),
            "DATABASE_URL": f"sqlite:///{database_path}",
            "STORAGE_DIR": str(storage_dir),
            "ARTIFACT_BASE_URL": base_url,
            "API_HOST": "127.0.0.1",
            "API_PORT": str(port),
        }
    )
    return secrets_path


def packaged_backend_root() -> Path:
    bundle_root = getattr(sys, "_MEIPASS", None)
    return Path(bundle_root).resolve() if bundle_root else Path(__file__).resolve().parent


def run_migrations() -> None:
    """Upgrade the desktop SQLite database before accepting requests."""
    from alembic import command
    from alembic.config import Config

    migrations = packaged_backend_root() / "alembic"
    if not migrations.is_dir():
        raise RuntimeError(f"Alembic migrations are missing: {migrations}")
    config = Config()
    config.set_main_option("script_location", str(migrations))
    config.set_main_option("prepend_sys_path", str(packaged_backend_root()))
    command.upgrade(config, "head")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the AgentHub desktop backend")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--session-token", required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    if not 1024 <= args.port <= 65535:
        raise SystemExit("--port must be between 1024 and 65535")
    prepare_desktop_environment(args.data_dir, args.port, args.session_token)
    run_migrations()

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=args.port,
        reload=False,
        log_config=None,
        access_log=False,
    )


if __name__ == "__main__":
    main()
