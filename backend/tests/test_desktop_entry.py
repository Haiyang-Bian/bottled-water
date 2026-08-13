from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from desktop_entry import parse_args, prepare_desktop_environment
from app.core.config import Settings


pytestmark = [pytest.mark.desktop, pytest.mark.unit]


def test_desktop_environment_uses_stable_secrets_and_local_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "development")

    with patch.dict(os.environ, {}, clear=False):
        secrets_path = prepare_desktop_environment(tmp_path, 18765)
        first = json.loads(secrets_path.read_text(encoding="utf-8"))
        prepare_desktop_environment(tmp_path, 18766)
        second = json.loads(secrets_path.read_text(encoding="utf-8"))

        assert first == second
        assert len(first["secret_key"]) == 64
        assert len(first["data_encryption_key"]) == 64
        assert first["secret_key"] not in str(secrets_path)
        assert os.environ["ENVIRONMENT"] == "desktop"
        assert os.environ["DATABASE_URL"].endswith("/agenthub.db")
        assert os.environ["ARTIFACT_BASE_URL"] == "http://127.0.0.1:18766"


def test_desktop_entry_requires_explicit_data_dir_and_port():
    args = parse_args(["--data-dir", "desktop-data", "--port", "18000"])

    assert args.data_dir.name == "desktop-data"
    assert args.port == 18000


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
