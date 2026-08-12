from __future__ import annotations

import uuid
from typing import Any

from app.core.config import Settings


def unwrap(body: dict[str, Any]) -> Any:
    return body.get("data", body)


def register_user(client: Any, prefix: str) -> tuple[dict[str, Any], dict[str, str]]:
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}-{suffix}@example.com",
            "username": f"{prefix}_{suffix}",
            "display_name": prefix,
            "password": "Original123!",
        },
    )
    assert response.status_code == 200, response.text
    data = unwrap(response.json())
    return data["user"], {"Authorization": f"Bearer {data['access_token']}"}


def test_registered_member_gets_database_role_and_cannot_manage_users(client: Any) -> None:
    _user, headers = register_user(client, "member")
    permissions = client.get("/api/v1/permissions/me", headers=headers)
    assert permissions.status_code == 200, permissions.text
    data = unwrap(permissions.json())
    assert data["roles"] == ["ROLE_USER"]
    assert "user:manage" not in data["permissions"]

    users = client.get("/api/v1/security/users", headers=headers)
    assert users.status_code == 403


def test_production_settings_reject_debug_and_placeholder_secret() -> None:
    try:
        Settings(environment="production", debug=True, secret_key="change-me-in-production")
    except ValueError as exc:
        assert "DEBUG" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("Production settings accepted insecure defaults")


def test_model_secrets_are_never_serialized(client: Any) -> None:
    _user, headers = register_user(client, "model-secret")
    created = client.post(
        "/api/v1/model-providers",
        json={
            "name": "Private Provider",
            "provider_type": "openai_compatible",
            "base_url": "https://example.invalid/v1",
            "api_key": "secret-provider-key",
            "default_model": "private-model",
            "config": {"nested": {"access_token": "secret-token"}},
        },
        headers=headers,
    )
    assert created.status_code == 200, created.text
    provider = unwrap(created.json())
    assert provider["api_key_set"] is True
    assert "secret-provider-key" not in created.text
    assert "secret-token" not in created.text

    credential = client.patch(
        f"/api/v1/model-providers/{provider['id']}/credential",
        json={"api_key": "replacement-secret-key"},
        headers=headers,
    )
    assert credential.status_code == 200, credential.text
    assert "replacement-secret-key" not in credential.text

    config = client.post(
        "/api/v1/model-configs",
        json={
            "provider_id": provider["id"],
            "name": "Private Model",
            "model_id": "private-model",
            "config": {"api_key": "must-not-round-trip", "thinking": False},
        },
        headers=headers,
    )
    assert config.status_code == 200, config.text
    assert "must-not-round-trip" not in config.text
    assert unwrap(config.json())["config"] == {"thinking": False}
