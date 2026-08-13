from typing import Any
import uuid


def unwrap(body: dict[str, Any]) -> Any:
    return body.get("data", body)


def test_create_and_list_group_conversation(
    client: Any,
    api_paths: dict[str, str],
    auth_headers: dict[str, str],
    conversation_id: str,
) -> None:
    response = client.get(api_paths["conversations"], headers=auth_headers)

    assert response.status_code == 200, response.text
    body = response.json()
    conversations = body.get("items", body if isinstance(body, list) else [])
    assert any(str(item.get("id") or item.get("conversation_id")) == conversation_id for item in conversations)

    renamed = client.patch(
        f"/api/v1/conversations/{conversation_id}",
        json={"title": "Acceptance Factory", "folder": "Factory", "remark": "demo remark"},
        headers=auth_headers,
    )
    assert renamed.status_code == 200, renamed.text
    renamed_body = renamed.json().get("data", renamed.json())
    assert renamed_body["folder"] == "Factory"
    assert renamed_body["remark"] == "demo remark"

    workflow_run = client.post(f"/api/v1/conversations/{conversation_id}/workflow/runs", json={}, headers=auth_headers)
    assert workflow_run.status_code == 200, workflow_run.text
    run = workflow_run.json().get("data", workflow_run.json())
    first_node = run["node_states"][0]["id"]
    updated = client.patch(
        f"/api/v1/conversations/{conversation_id}/workflow/runs/{run['id']}/nodes/{first_node}",
        json={"status": "completed", "progress": 100, "output": {"summary": "done"}},
        headers=auth_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json().get("data", updated.json())["progress"] >= 0


def test_conversations_are_isolated_by_workspace_and_workflow_matches_group_agents(
    client: Any,
    auth_headers: dict[str, str],
) -> None:
    left = client.post(
        "/api/v1/workspaces",
        json={"name": f"Left Workspace {uuid.uuid4().hex[:8]}", "description": "left", "type": "custom"},
        headers=auth_headers,
    )
    right = client.post(
        "/api/v1/workspaces",
        json={"name": f"Right Workspace {uuid.uuid4().hex[:8]}", "description": "right", "type": "custom"},
        headers=auth_headers,
    )
    assert left.status_code == 200, left.text
    assert right.status_code == 200, right.text
    left_id = unwrap(left.json())["id"]
    right_id = unwrap(right.json())["id"]

    created_left = client.post(
        "/api/v1/conversations",
        json={"title": "Left group", "chat_type": "group", "workspace_id": left_id},
        headers=auth_headers,
    )
    created_right = client.post(
        "/api/v1/conversations",
        json={"title": "Right group", "chat_type": "group", "workspace_id": right_id},
        headers=auth_headers,
    )
    assert created_left.status_code == 200, created_left.text
    assert created_right.status_code == 200, created_right.text
    left_conversation = unwrap(created_left.json())
    right_conversation = unwrap(created_right.json())
    assert str(left_conversation["conversation_number"]).isdigit()
    assert len(str(left_conversation["conversation_number"])) == 12
    assert left_conversation["group_number"] == left_conversation["conversation_number"]
    assert left_conversation["message_count"] == 0

    left_messages = client.get(
        f"/api/v1/conversations/{left_conversation['id']}/messages",
        headers=auth_headers,
    )
    assert left_messages.status_code == 200, left_messages.text
    assert unwrap(left_messages.json())["items"] == []

    left_list = client.get(f"/api/v1/conversations?workspace_id={left_id}", headers=auth_headers)
    right_list = client.get(f"/api/v1/conversations?workspace_id={right_id}", headers=auth_headers)
    assert left_list.status_code == 200, left_list.text
    assert right_list.status_code == 200, right_list.text
    left_items = unwrap(left_list.json())["items"]
    right_items = unwrap(right_list.json())["items"]
    assert any(item["id"] == left_conversation["id"] for item in left_items)
    assert all(item["id"] != right_conversation["id"] for item in left_items)
    assert any(item["id"] == right_conversation["id"] for item in right_items)

    workflow = client.get(f"/api/v1/conversations/{left_conversation['id']}/workflow", headers=auth_headers)
    assert workflow.status_code == 200, workflow.text
    workflow_body = unwrap(workflow.json())
    assert workflow_body["mode"] == "all_agents_independent"
    assert len(workflow_body["nodes"]) == left_conversation["agent_count"] + 2
    assert {node["type"] for node in workflow_body["nodes"]} >= {"start", "agent", "end"}
    assert workflow_body["edges"]


def test_workflow_preserves_dify_style_node_config_and_runtime_state(
    client: Any,
    auth_headers: dict[str, str],
    conversation_id: str,
) -> None:
    workflow = {
        "mode": "manual",
        "nodes": [
            {"id": "start", "title": "Start", "type": "start", "role": "start", "status": "ready", "config": {}},
            {
                "id": "cond",
                "title": "Need review?",
                "type": "condition",
                "role": "condition",
                "status": "ready",
                "config": {"expression": "input.includes('review')", "branches": ["review", "skip"]},
            },
            {
                "id": "loop",
                "title": "Iterate",
                "type": "loop",
                "role": "loop",
                "status": "ready",
                "config": {"max_iterations": 4},
            },
            {
                "id": "artifact",
                "title": "Export",
                "type": "artifact",
                "role": "artifact",
                "status": "ready",
                "config": {"artifact_type": "pdf"},
            },
            {"id": "end", "title": "End", "type": "end", "role": "end", "status": "ready", "config": {}},
        ],
        "edges": [["start", "cond"], ["cond", "loop"], ["loop", "artifact"], ["artifact", "end"]],
        "settings": {"edited_by": "test"},
    }
    saved = client.patch(f"/api/v1/conversations/{conversation_id}/workflow", json=workflow, headers=auth_headers)
    assert saved.status_code == 200, saved.text
    saved_body = unwrap(saved.json())
    assert saved_body["nodes"][1]["type"] == "condition"
    assert saved_body["nodes"][1]["config"]["expression"] == "input.includes('review')"
    assert saved_body["nodes"][2]["config"]["max_iterations"] == 4

    run_response = client.post(f"/api/v1/conversations/{conversation_id}/workflow/runs", json={}, headers=auth_headers)
    assert run_response.status_code == 200, run_response.text
    run = unwrap(run_response.json())
    by_id = {node["id"]: node for node in run["node_states"]}
    assert by_id["cond"]["type"] == "condition"
    assert by_id["cond"]["output"]["matched_branch"] == "review"
    assert by_id["loop"]["type"] == "loop"
    assert by_id["loop"]["output"]["max_iterations"] == 4


def test_workflow_update_accepts_canvas_object_edges_and_enables_runtime(
    client: Any,
    auth_headers: dict[str, str],
) -> None:
    created = client.post(
        "/api/v1/conversations",
        json={"title": "Canvas object edge group", "chat_type": "group"},
        headers=auth_headers,
    )
    assert created.status_code == 200, created.text
    conversation_id = unwrap(created.json())["id"]
    workflow = {
        "mode": "manual",
        "nodes": [
            {"id": "start", "title": "Start", "type": "start", "status": "ready"},
            {"id": "end", "title": "End", "type": "end", "status": "ready"},
        ],
        "edges": [
            {
                "source": "start",
                "target": "end",
                "sourceHandle": "output",
                "targetHandle": "input",
                "config": {"source_handle": "output", "target_handle": "input"},
            }
        ],
        "settings": {"enabled": True},
    }

    saved = client.patch(
        f"/api/v1/conversations/{conversation_id}/workflow",
        json=workflow,
        headers=auth_headers,
    )

    assert saved.status_code == 200, saved.text
    saved_body = unwrap(saved.json())
    assert saved_body["settings"]["enabled"] is True
    assert saved_body["edges"][0]["from"] == "start"
    assert saved_body["edges"][0]["to"] == "end"
    assert saved_body["edges"][0]["sourceHandle"] == "output"

    conversations = client.get("/api/v1/conversations", headers=auth_headers)
    assert conversations.status_code == 200, conversations.text
    current = next(item for item in unwrap(conversations.json())["items"] if item["id"] == conversation_id)
    assert current["workflow_enabled"] is True
    assert current["scheduling_strategy"] == "workflow"
    assert "runtime_mode" not in current
