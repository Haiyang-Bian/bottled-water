from __future__ import annotations

import uuid
from typing import Any

import pytest


pytestmark = [pytest.mark.integration, pytest.mark.providers]


def unwrap(body: dict[str, Any]) -> Any:
    return body.get("data", body)


def register(client: Any, prefix: str) -> dict[str, str]:
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": f"{prefix}-{suffix}@example.com",
            "username": f"{prefix}_{suffix}",
            "password": "Original123!",
        },
    )
    assert response.status_code == 200, response.text
    return {
        "Authorization": f"Bearer {unwrap(response.json())['access_token']}"
    }


def create_deepseek_config(client: Any, headers: dict[str, str]) -> dict[str, Any]:
    response = client.post(
        "/api/v1/model-configs",
        json={
            "provider_type": "deepseek",
            "name": "DeepSeek V4 Flash",
            "model_id": "deepseek-v4-flash",
            "context_window": 1000000,
            "max_output_tokens": 8192,
            "config": {"thinking_enabled": False, "reasoning_effort": "high"},
        },
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return unwrap(response.json())


def test_builtin_metadata_and_user_owned_provider_isolation(client: Any) -> None:
    first_headers = register(client, "deepseek-one")
    second_headers = register(client, "deepseek-two")

    builtin = client.get("/api/v1/model-providers/builtin")
    assert builtin.status_code == 200, builtin.text
    deepseek = next(
        item
        for item in unwrap(builtin.json())["items"]
        if item["provider_type"] == "deepseek"
    )
    assert deepseek["default_model"] == "deepseek-v4-flash"
    assert deepseek["supports_thinking"] is True

    first_config = create_deepseek_config(client, first_headers)
    second_config = create_deepseek_config(client, second_headers)
    assert first_config["provider_id"] != second_config["provider_id"]

    first_providers = client.get("/api/v1/model-providers", headers=first_headers)
    assert first_providers.status_code == 200, first_providers.text
    first_provider_ids = {
        item["id"] for item in unwrap(first_providers.json())["items"]
    }
    assert first_config["provider_id"] in first_provider_ids
    assert second_config["provider_id"] not in first_provider_ids

    forbidden_test = client.post(
        "/api/v1/model-configs/test",
        json={"model_config_id": second_config["id"], "prompt": "hello"},
        headers=first_headers,
    )
    assert forbidden_test.status_code == 403


def test_deepseek_credential_and_available_models_never_echo_secret(client: Any) -> None:
    headers = register(client, "deepseek-credential")
    config = create_deepseek_config(client, headers)

    credential = client.patch(
        f"/api/v1/model-providers/{config['provider_id']}/credential",
        json={"api_key": "deepseek-secret-key"},
        headers=headers,
    )
    assert credential.status_code == 200, credential.text
    assert "deepseek-secret-key" not in credential.text

    providers = client.get("/api/v1/model-providers", headers=headers)
    assert providers.status_code == 200, providers.text
    provider = next(
        item
        for item in unwrap(providers.json())["items"]
        if item["id"] == config["provider_id"]
    )
    assert provider["api_key_set"] is True
    assert "deepseek-secret-key" not in providers.text

    available = client.get("/api/v1/models/available", headers=headers)
    assert available.status_code == 200, available.text
    assert any(
        item["model_id"] == "deepseek-v4-flash"
        for item in unwrap(available.json())["items"]
    )
