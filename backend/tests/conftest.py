"""pytest 全局配置。"""

import importlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

# 将 src/ 加入 Python 路径，确保可以导入 agent_runtime 等模块。
_SRC_DIR = Path(__file__).parent.parent / "src"
_TESTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SRC_DIR))
sys.path.insert(0, str(Path(__file__).parents[2] / "src"))
sys.path.insert(0, str(_TESTS_DIR))


DEFAULT_APP_CANDIDATES = ("app:app",)


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live-base-url",
        action="store",
        default=os.getenv("AGENTHUB_API_BASE_URL"),
        help="Run API acceptance tests against a live AgentHub backend.",
    )


def _load_asgi_app() -> Any:
    target = os.getenv("AGENTHUB_BACKEND_APP")
    candidates = (target,) if target else DEFAULT_APP_CANDIDATES

    errors: list[str] = []
    for candidate in candidates:
        if not candidate:
            continue
        module_name, _, app_name = candidate.partition(":")
        app_name = app_name or "app"
        try:
            module = importlib.import_module(module_name)
            return getattr(module, app_name)
        except Exception as exc:  # pragma: no cover - only used for diagnostics.
            errors.append(f"{candidate}: {exc}")

    pytest.skip(
        "No backend app found. Set AGENTHUB_BACKEND_APP='module:app' "
        "or AGENTHUB_API_BASE_URL for live API tests. Tried: "
        + "; ".join(errors)
    )


@pytest.fixture(scope="session")
def api_base_url(pytestconfig: pytest.Config) -> str | None:
    value = pytestconfig.getoption("--live-base-url")
    return value.rstrip("/") if value else None


@pytest.fixture(scope="session")
def api_paths() -> dict[str, str]:
    return {
        "signup": os.getenv("AGENTHUB_PATH_SIGNUP", "/auth/signup"),
        "login": os.getenv("AGENTHUB_PATH_LOGIN", "/auth/login"),
        "me": os.getenv("AGENTHUB_PATH_ME", "/auth/me"),
        "conversations": os.getenv("AGENTHUB_PATH_CONVERSATIONS", "/conversations"),
        "messages": os.getenv("AGENTHUB_PATH_MESSAGES", "/conversations/{conversation_id}/messages"),
        "orchestrator_tasks": os.getenv("AGENTHUB_PATH_TASKS", "/orchestrator/tasks"),
        "artifacts": os.getenv("AGENTHUB_PATH_ARTIFACTS", "/artifacts"),
        "deployments": os.getenv("AGENTHUB_PATH_DEPLOYMENTS", "/deployments"),
    }


@pytest.fixture(scope="session")
def client(api_base_url: str | None) -> Iterator[Any]:
    if api_base_url:
        import httpx

        with httpx.Client(base_url=api_base_url, timeout=20.0) as live_client:
            yield live_client
        return

    try:
        from fastapi.testclient import TestClient
    except Exception:
        pytest.skip("fastapi is not installed and no live API base URL was provided.")

    app = _load_asgi_app()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def auth_headers(client: Any, api_paths: dict[str, str]) -> dict[str, str]:
    email = os.getenv("AGENTHUB_TEST_EMAIL", "acceptance@example.com")
    password = os.getenv("AGENTHUB_TEST_PASSWORD", "Acceptance123!")
    payload = {"email": email, "password": password, "name": "Acceptance User"}

    signup = client.post(api_paths["signup"], json=payload)
    assert signup.status_code in {200, 201, 204, 409}, signup.text

    login = client.post(api_paths["login"], json={"email": email, "password": password})
    assert login.status_code == 200, login.text
    body = login.json()
    token = body.get("access_token") or body.get("token")
    assert token, f"Login response must include access_token or token: {body}"
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def conversation_id(client: Any, api_paths: dict[str, str], auth_headers: dict[str, str]) -> str:
    response = client.post(
        api_paths["conversations"],
        json={"title": "Acceptance group chat", "type": "group"},
        headers=auth_headers,
    )
    assert response.status_code in {200, 201}, response.text
    body = response.json()
    value = body.get("id") or body.get("conversation_id")
    assert value, f"Conversation response must include id or conversation_id: {body}"
    return str(value)
