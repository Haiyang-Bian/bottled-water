import uuid
from typing import Any
from urllib.parse import urlparse


def unwrap(body: dict[str, Any]) -> Any:
    return body.get("data", body)


def test_workspace_project_permissions_and_audit(client: Any, auth_headers: dict[str, str]) -> None:
    permissions = client.get("/api/v1/permissions/me", headers=auth_headers)
    assert permissions.status_code == 200, permissions.text
    assert "workspace:create" in unwrap(permissions.json())["permissions"]

    workspace = client.post(
        "/api/v1/workspaces",
        json={
            "name": f"Acceptance Workspace {uuid.uuid4().hex[:8]}",
            "description": "Workspace smoke for platform-scale management",
            "type": "vertical",
            "tags": ["acceptance", "workspace"],
            "config": {"template_id": "fullstack-delivery"},
        },
        headers=auth_headers,
    )
    assert workspace.status_code == 200, workspace.text
    workspace_id = unwrap(workspace.json())["id"]

    project = client.post(
        f"/api/v1/workspaces/{workspace_id}/projects",
        json={"name": f"Acceptance Project {uuid.uuid4().hex[:8]}", "type": "code_project"},
        headers=auth_headers,
    )
    assert project.status_code == 200, project.text
    project_id = unwrap(project.json())["id"]

    saved = client.put(
        f"/api/v1/projects/{project_id}/files",
        json={"path": "src/main.ts", "language": "typescript", "content": "export const ok = true;"},
        headers=auth_headers,
    )
    assert saved.status_code == 200, saved.text
    assert unwrap(saved.json())["version"] == 1

    logs = client.get("/api/v1/audit-logs", headers=auth_headers)
    assert logs.status_code == 200, logs.text
    assert unwrap(logs.json())["items"]

    roles = client.get("/api/v1/security/roles", headers=auth_headers)
    assert roles.status_code == 200, roles.text
    assert unwrap(roles.json())["total"] >= 1

    users = client.get("/api/v1/security/users", headers=auth_headers)
    assert users.status_code == 403, users.text

    stats = client.get("/api/v1/audit-logs/stats", headers=auth_headers)
    assert stats.status_code == 200, stats.text
    assert "total" in unwrap(stats.json())


def test_member_cannot_update_user_roles(client: Any, auth_headers: dict[str, str]) -> None:
    suffix = uuid.uuid4().hex[:8]
    email = f"rbac-{suffix}@example.com"
    signup = client.post(
        "/auth/signup",
        json={"email": email, "password": "Acceptance123!", "name": f"rbac_{suffix}"},
    )
    assert signup.status_code in {200, 201, 409}, signup.text
    target_user = unwrap(signup.json())["user"]

    updated = client.patch(
        f"/api/v1/security/users/{target_user['id']}/role",
        json={"role": "developer"},
        headers=auth_headers,
    )
    assert updated.status_code == 403, updated.text


def test_task_approval_and_deployment_operations(
    client: Any,
    auth_headers: dict[str, str],
    conversation_id: str,
) -> None:
    task = client.post(
        "/api/v1/tasks",
        json={"conversation_id": conversation_id, "title": "Build approval workflow", "description": "Create task"},
        headers=auth_headers,
    )
    assert task.status_code == 200, task.text
    task_id = unwrap(task.json())["id"]

    status = client.get(f"/api/v1/tasks/{task_id}/status", headers=auth_headers)
    assert status.status_code == 200, status.text
    assert "progress" in unwrap(status.json())

    subtasks = client.get(f"/api/v1/tasks/{task_id}/subtasks", headers=auth_headers)
    assert subtasks.status_code == 200, subtasks.text
    first_subtask = unwrap(subtasks.json())[0]

    approved = client.post(
        f"/api/v1/subtasks/{first_subtask['id']}/approve",
        json={"comment": "Looks good"},
        headers=auth_headers,
    )
    assert approved.status_code == 200, approved.text
    assert unwrap(approved.json())["status"] == "APPROVED"

    artifact = client.post(
        "/api/v1/artifacts",
        json={
            "conversation_id": conversation_id,
            "title": "Deployable Acceptance Artifact",
            "content": {"files": {"index.html": "<main>deploy</main>"}},
        },
        headers=auth_headers,
    )
    assert artifact.status_code == 200, artifact.text
    artifact_id = unwrap(artifact.json())["id"]

    deployment = client.post(
        "/api/v1/deployments",
        json={"artifact_id": artifact_id, "mode": "preview_link"},
        headers=auth_headers,
    )
    assert deployment.status_code == 200, deployment.text
    deployment_body = unwrap(deployment.json())
    deployment_id = deployment_body["id"]
    assert deployment_body["status"] == "deployed"
    assert deployment_body["health_status"] == "healthy"
    assert deployment_body["health"]["checks"]
    site_path = urlparse(deployment_body["access_url"]).path
    site = client.get(site_path)
    assert site.status_code == 200, site.text
    assert "deploy" in site.text

    logs = client.get(f"/api/v1/deployments/{deployment_id}/logs", headers=auth_headers)
    assert logs.status_code == 200, logs.text
    log_items = unwrap(logs.json())["items"]
    assert log_items
    assert any("访问入口" in item["message"] for item in log_items)

    health = client.post(f"/api/v1/deployments/{deployment_id}/health", headers=auth_headers)
    assert health.status_code == 200, health.text
    assert unwrap(health.json())["health_status"] == "healthy"

    parsed = client.post("/api/v1/deployments/parse-command", json={"message": "请部署到预览链接"}, headers=auth_headers)
    assert parsed.status_code == 200, parsed.text
    assert unwrap(parsed.json())["recognized"] is True

    container_deployment = client.post(
        "/api/v1/deployments",
        json={"artifact_id": artifact_id, "mode": "container"},
        headers=auth_headers,
    )
    assert container_deployment.status_code == 200, container_deployment.text
    container_body = unwrap(container_deployment.json())
    assert container_body["status"] == "deployed"
    assert container_body["health_status"] == "healthy"
    assert container_body["mode"] == "container"
    assert container_body["access_url"]
    assert "container 模式" in container_body["health"]["checks"][1]["message"]

    rollback = client.post(f"/api/v1/deployments/{deployment_id}/rollback", json={}, headers=auth_headers)
    assert rollback.status_code == 200, rollback.text
    assert unwrap(rollback.json())["status"] == "deployed"
