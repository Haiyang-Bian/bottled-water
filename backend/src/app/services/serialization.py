from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from db.models import (
    Agent,
    Artifact,
    FileAsset,
    Conversation,
    ConversationParticipant,
    Deployment,
    KnowledgeBase,
    KnowledgeDocument,
    Message,
    McpToolInvocation,
    McpServer,
    ModelConfig,
    ModelProvider,
    Project,
    ProjectFile,
    PromptTemplate,
    ShortcutCommand,
    Skill,
    SkillRun,
    Subtask,
    Task,
    ToolDefinition,
    ToolInvocation,
    SandboxSession,
    RemoteConnection,
    User,
    Workspace,
    WorkspaceMember,
    WorkflowRun,
)
from app.services.conversation_identity import conversation_group_number, conversation_number


def iso(value: datetime | None) -> str | None:
    if not value:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


SENSITIVE_KEY_PARTS = ("api_key", "apikey", "secret", "token", "password", "authorization")


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                result[key] = "***"
            else:
                result[key] = redact_sensitive(item)
        return result
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value


def strip_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                continue
            result[key] = strip_sensitive(item)
        return result
    if isinstance(value, list):
        return [strip_sensitive(item) for item in value]
    return value


def user_to_dict(user: User) -> dict[str, Any]:
    extra = user.extra or {}
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "name": user.display_name,
        "display_name": user.display_name,
        "avatar": user.avatar_url,
        "avatar_url": user.avatar_url,
        "signature": str(extra.get("signature") or ""),
        "role": user.role,
        "default_model_config_id": extra.get("default_model_config_id"),
    }


def agent_to_dict(agent: Agent) -> dict[str, Any]:
    config = agent.config or {}
    extra = agent.extra or {}
    raw_capabilities = agent.capabilities or []
    capabilities = []
    for index, item in enumerate(raw_capabilities):
        if isinstance(item, dict):
            capabilities.append(
                {
                    "id": item.get("id") or f"cap-{agent.id[:8]}-{index}",
                    "label": item.get("label") or item.get("name") or str(item),
                    "category": item.get("category") or "通用",
                    "proficiency": int(item.get("proficiency") or 3),
                }
            )
        else:
            capabilities.append(
                {
                    "id": f"cap-{agent.id[:8]}-{index}",
                    "label": str(item),
                    "category": "通用",
                    "proficiency": 3,
                }
            )
    return {
        "id": agent.id,
        "name": agent.name,
        "display_name": extra.get("display_name") or agent.name,
        "type": agent.type,
        "version": agent.version,
        "status": agent.status,
        "status_detail": extra.get("status_detail"),
        "description": agent.description,
        "avatar_url": agent.avatar_url,
        "avatar_color": extra.get("avatar_color") or "#1677ff",
        "icon_url": extra.get("icon_url"),
        "capabilities": capabilities,
        "supported_content_types": config.get(
            "supported_content_types", ["text", "code", "image", "file", "card", "diff"]
        ),
        "provider": extra.get("provider") or ("custom" if agent.type == "custom" else agent.type),
        "is_official": not bool(agent.owner_id) or agent.type != "custom",
        "created_by": agent.owner_id,
        "last_heartbeat_at": iso(agent.last_heartbeat_at),
        "response_latency_ms": int(extra.get("response_latency_ms") or 900),
        "config": {
            "max_context_tokens": config.get("max_context_tokens", 128000),
            "max_output_tokens": config.get("max_output_tokens", 8192),
            "supports_streaming": config.get("supports_streaming", True),
            "supports_vision": config.get("supports_vision", False),
            "supports_tool_use": config.get("supports_tool_use", True),
            "supports_file_upload": config.get("supports_file_upload", True),
            "rate_limit_rpm": config.get("rate_limit_rpm", 60),
            "rate_limit_tpm": config.get("rate_limit_tpm", 200000),
            "temperature": config.get("temperature", 0.7),
            "custom_prompt_prefix": config.get("system_prompt") or config.get("custom_prompt_prefix"),
            "custom_parameters": config.get("custom_parameters", {}),
            "tools": config.get("tools", []),
            "skill_ids": config.get("skill_ids", []),
             "mcp_server_ids": config.get("mcp_server_ids", []),
             "capability_permissions_initialized": config.get("capability_permissions_initialized", False),
             "agentic_loop": config.get("agentic_loop", {}),
            "base_agent_id": config.get("base_agent_id"),
            "model_config_id": config.get("model_config_id"),
            "model_id": config.get("model_id"),
            "provider_id": config.get("provider_id"),
        },
        "stats": {
            "total_conversations": extra.get("total_conversations", 0),
            "total_messages": extra.get("total_messages", 0),
            "total_tokens_consumed": extra.get("total_tokens_consumed", 0),
            "avg_response_time_ms": extra.get("avg_response_time_ms", extra.get("response_latency_ms", 900)),
            "success_rate": extra.get("success_rate", 0.98),
            "last_active_at": extra.get("last_active_at") or iso(agent.updated_at),
        },
        "tags": [item["label"] for item in capabilities],
        "created_at": iso(agent.created_at),
        "updated_at": iso(agent.updated_at),
    }


def participant_to_dict(participant: ConversationParticipant) -> dict[str, Any]:
    agent = participant.agent
    return {
        "id": participant.id,
        "participant_type": participant.participant_type,
        "user_id": participant.user_id,
        "agent_id": participant.agent_id,
        "agent_name": agent.name if agent else participant.nickname,
        "agent_type": agent.type if agent else None,
        "agent_avatar_url": agent.avatar_url if agent else None,
        "agent_status": agent.status if agent else None,
        "role": participant.role,
        "nickname": participant.nickname,
        "unread_count": participant.unread_count,
        "left_at": iso(participant.left_at),
        "joined_at": iso(participant.joined_at),
    }


def conversation_to_dict(conversation: Conversation) -> dict[str, Any]:
    active_participants = [item for item in conversation.participants if item.left_at is None]
    participants = [participant_to_dict(item) for item in active_participants]
    participant_names = [
        item["agent_name"] for item in participants if isinstance(item.get("agent_name"), str)
    ]
    tags = []
    for item in active_participants:
        if item.agent:
            for cap in (item.agent.capabilities or [])[:2]:
                if isinstance(cap, dict):
                    label = cap.get("label") or cap.get("name") or cap.get("category")
                    if label:
                        tags.append(str(label))
                elif cap:
                    tags.append(str(cap))
    return {
        "id": conversation.id,
        "conversation_id": conversation.id,
        "chat_type": conversation.chat_type,
        "type": conversation.chat_type,
        "conversation_number": conversation_number(conversation),
        "group_number": conversation_group_number(conversation),
        "title": conversation.title,
        "description": conversation.description,
        "workspace_id": conversation.extra.get("workspace_id") if isinstance(conversation.extra, dict) else None,
        "avatar_url": conversation.avatar_url,
        "participants": participants,
        "participantNames": participant_names,
        "participant_count": len(participants),
        "agent_count": len([item for item in participants if item.get("participant_type") == "agent"]),
        "user_count": len([item for item in participants if item.get("participant_type") == "user"]),
        "master_enabled": bool(
            conversation.extra.get(
                "master_enabled",
                len([p for p in active_participants if p.participant_type == "agent"]) > 1,
            )
        ),
        "max_participants": conversation.extra.get("max_participants", 8),
        "status": conversation.status,
        "is_pinned": conversation.is_pinned,
        "pinned": conversation.is_pinned,
        "pinned_at": iso(conversation.pinned_at),
        "unread_count": conversation.unread_count,
        "unread": conversation.unread_count,
        "last_message_preview": conversation.last_message_preview,
        "lastMessage": conversation.last_message_preview,
        "last_message_sender": conversation.last_message_sender,
        "last_message_at": iso(conversation.last_message_at),
        "updatedAt": iso(conversation.updated_at),
        "generation_status": conversation.generation_status,
        "active_run_id": conversation.active_run_id,
        "scheduling_strategy": conversation.extra.get("scheduling_strategy") if isinstance(conversation.extra, dict) else None,
        "workflow_enabled": bool(conversation.extra.get("workflow_enabled")) if isinstance(conversation.extra, dict) else False,
        "team_settings": conversation.extra.get("team_settings") if isinstance(conversation.extra, dict) else None,
        "activity_score": conversation.activity_score,
        "message_count": conversation.message_count,
        "archived": conversation.status == "archived",
        "tags": list(dict.fromkeys(tags))[:4],
        "category": conversation.extra.get("category", "Default") if isinstance(conversation.extra, dict) else "Default",
        "folder": conversation.extra.get("folder", "Default") if isinstance(conversation.extra, dict) else "Default",
        "remark": conversation.extra.get("remark", "") if isinstance(conversation.extra, dict) else "",
        "workflow": conversation.extra.get("workflow") if isinstance(conversation.extra, dict) else None,
        "workflow_runtime": conversation.extra.get("workflow_runtime") if isinstance(conversation.extra, dict) else None,
        "runtime": redact_sensitive(conversation.extra.get("runtime")) if isinstance(conversation.extra, dict) else None,
        "created_at": iso(conversation.created_at),
        "updated_at": iso(conversation.updated_at),
    }


def message_meta_to_dict(message: Message) -> dict[str, Any]:
    """只返回消息元数据，用于流结束后的 message:updated 事件。

    不包含 content、thinking 等已在流式传输过程中传递给前端的内容字段，
    避免前端已累积的流式内容被覆盖。
    """
    return {
        "id": message.id,
        "status": message.status,
        "version_count": message.version_count,
        "current_version": message.current_version,
        "created_at": iso(message.created_at),
        "updated_at": iso(message.updated_at),
    }


def message_to_dict(message: Message) -> dict[str, Any]:
    text = message.content.get("text") or message.content.get("code") or ""
    attachments = message.content.get("attachments") or []
    if not text and message.content_type == "preview_card":
        text = f"预览产物：{message.content.get('title', '')}"
    if not text and message.content_type == "deploy_status_card":
        text = f"部署状态：{message.content.get('status', '')}"
    return {
        "id": message.id,
        "message_id": message.id,
        "client_message_id": message.client_message_id,
        "conversation_id": message.conversation_id,
        "conversationId": message.conversation_id,
        "sender_type": message.sender_type,
        "sender_id": message.sender_id,
        "sender_name": message.sender_name,
        "sender_avatar_url": message.sender_avatar_url,
        "role": "assistant" if message.sender_type == "agent" else message.sender_type,
        "author": message.sender_name or message.sender_type,
        "content_type": message.content_type,
        "kind": message.content_type,
        "content": text,
        "rawContent": message.content,
        "attachments": attachments,
        "thinking": message.content.get("thinking") if isinstance(message.content, dict) else None,
        "status": message.status,
        "reply_to_message_id": message.reply_to_message_id,
        "quotedMessageId": message.reply_to_message_id,
        "version_count": message.version_count,
        "current_version": message.current_version,
        "created_at": iso(message.created_at),
        "createdAt": iso(message.created_at),
        "updated_at": iso(message.updated_at),
    }


def task_to_dict(task: Task) -> dict[str, Any]:
    return {
        "id": task.id,
        "task_id": task.id,
        "conversation_id": task.conversation_id,
        "title": task.title,
        "description": task.description,
        "status": task.status,
        "priority": task.priority,
        "progress": task.progress,
        "plan": task.plan,
        "output": task.output,
        "created_at": iso(task.created_at),
        "updated_at": iso(task.updated_at),
    }


def subtask_to_dict(subtask: Subtask) -> dict[str, Any]:
    return {
        "id": subtask.id,
        "subtask_id": subtask.id,
        "parent_task_id": subtask.parent_task_id,
        "title": subtask.title,
        "description": subtask.description,
        "status": subtask.status,
        "order_index": subtask.order_index,
        "agent_id": subtask.agent_id,
        "agent_name": subtask.agent.name if subtask.agent else None,
        "output": subtask.output,
        "created_at": iso(subtask.created_at),
        "updated_at": iso(subtask.updated_at),
    }


def artifact_to_dict(artifact: Artifact) -> dict[str, Any]:
    content = artifact.content if isinstance(artifact.content, dict) else {}
    files = content.get("files") or {}
    html = files.get("index.html") or content.get("preview_html") or content.get("html") or ""
    first_code = next(iter(files.values()), html)
    previous_files = content.get("previous_files") or {}
    previous_code = previous_files.get("index.html") or content.get("previous_html") or first_code
    source_file = content.get("export_file") or content.get("source_file")
    source_file = source_file if isinstance(source_file, dict) else {}
    source_filename = str(source_file.get("filename") or "")
    artifact_format = str(
        content.get("format")
        or (content.get("tool_output") or {}).get("format")
        or source_file.get("format")
        or Path(source_filename).suffix.lstrip(".")
        or ""
    ).lower()
    filename = str(
        content.get("filename")
        or (content.get("tool_output") or {}).get("filename")
        or source_filename
        or artifact.name
    )
    media_type = str(
        content.get("media_type")
        or (content.get("tool_output") or {}).get("media_type")
        or source_file.get("media_type")
        or artifact.mime_type
        or ""
    )
    return {
        "id": artifact.id,
        "artifact_id": artifact.id,
        "conversation_id": artifact.conversation_id,
        "conversationId": artifact.conversation_id,
        "task_id": artifact.task_id,
        "agent_id": artifact.agent_id,
        "type": artifact.type,
        "kind": "preview" if artifact.type in {"web_app", "webpage", "html"} else artifact.type,
        "name": artifact.name,
        "title": artifact.name,
        "description": artifact.description,
        "status": artifact.status,
        "storage_url": artifact.storage_url,
        "preview_url": f"/api/v1/artifacts/{artifact.id}/preview",
        "export_url": f"/api/v1/artifacts/{artifact.id}/export" + (f"?format={artifact_format}" if artifact_format else ""),
        "format": artifact_format,
        "filename": filename,
        "media_type": media_type,
        "content": content,
        "files": files,
        "code": html or first_code,
        "previousCode": previous_code,
        "language": "html",
        "current_version": artifact.current_version,
        "updatedAt": iso(artifact.updated_at),
        "created_at": iso(artifact.created_at),
        "updated_at": iso(artifact.updated_at),
    }


def deployment_to_dict(deployment: Deployment) -> dict[str, Any]:
    health = (deployment.extra or {}).get("health") or {}
    return {
        "id": deployment.id,
        "deployment_id": deployment.id,
        "artifact_id": deployment.artifact_id,
        "mode": deployment.mode,
        "status": deployment.status,
        "access_url": deployment.access_url,
        "url": deployment.access_url,
        "deploy_log": deployment.deploy_log,
        "steps": deployment.steps or [],
        "error_message": deployment.error_message,
        "health": health,
        "health_status": health.get("status"),
        "last_health_check_at": health.get("checked_at"),
        "commit": deployment.id[:8],
        "deployed_at": iso(deployment.deployed_at),
        "updatedAt": iso(deployment.updated_at),
        "created_at": iso(deployment.created_at),
        "updated_at": iso(deployment.updated_at),
    }


def file_asset_to_dict(file_asset: FileAsset) -> dict[str, Any]:
    metadata = file_asset.extra or {}
    return {
        "id": file_asset.id,
        "file_id": file_asset.id,
        "conversation_id": file_asset.conversation_id,
        "message_id": file_asset.message_id,
        "artifact_id": file_asset.artifact_id,
        "workspace_id": metadata.get("workspace_id") if isinstance(metadata, dict) else None,
        "filename": file_asset.filename,
        "original_filename": file_asset.original_filename,
        "content_type": file_asset.content_type,
        "size": file_asset.size,
        "checksum": file_asset.checksum,
        "purpose": file_asset.purpose,
        "parse_status": file_asset.parse_status,
        "public_url": file_asset.public_url,
        "download_url": f"/api/v1/files/{file_asset.id}/download",
        "metadata": metadata,
        "created_at": iso(file_asset.created_at),
        "updated_at": iso(file_asset.updated_at),
    }


def knowledge_base_to_dict(kb: KnowledgeBase) -> dict[str, Any]:
    return {
        "id": kb.id,
        "knowledge_base_id": kb.id,
        "name": kb.name,
        "description": kb.description,
        "scope": kb.scope,
        "visibility": kb.visibility,
        "chunk_strategy": kb.chunk_strategy,
        "embedding_model": kb.embedding_model,
        "document_count": kb.document_count,
        "chunk_count": kb.chunk_count,
        "total_tokens": kb.total_tokens,
        "status": kb.status,
        "config": kb.config or {},
        "created_at": iso(kb.created_at),
        "updated_at": iso(kb.updated_at),
    }


def knowledge_document_to_dict(document: KnowledgeDocument) -> dict[str, Any]:
    return {
        "id": document.id,
        "document_id": document.id,
        "knowledge_base_id": document.knowledge_base_id,
        "file_asset_id": document.file_asset_id,
        "title": document.title,
        "source_type": document.source_type,
        "source_uri": document.source_uri,
        "token_count": document.token_count,
        "chunk_count": document.chunk_count,
        "index_status": document.index_status,
        "metadata": document.extra or {},
        "created_at": iso(document.created_at),
        "updated_at": iso(document.updated_at),
    }


def workspace_member_to_dict(member: WorkspaceMember) -> dict[str, Any]:
    return {
        "id": member.id,
        "workspace_id": member.workspace_id,
        "user_id": member.user_id,
        "user_name": member.user.display_name if member.user else None,
        "role": member.role,
        "permissions": member.permissions or [],
        "joined_at": iso(member.joined_at),
        "left_at": iso(member.left_at),
    }


def workspace_to_dict(workspace: Workspace) -> dict[str, Any]:
    members = [item for item in workspace.members if item.left_at is None]
    return {
        "id": workspace.id,
        "workspace_id": workspace.id,
        "name": workspace.name,
        "description": workspace.description,
        "type": workspace.type,
        "status": workspace.status,
        "avatar_color": workspace.avatar_color,
        "tags": workspace.tags or [],
        "config": workspace.config or {},
        "workflow": workspace.workflow or {},
        "resource_bindings": workspace.resource_bindings or {},
        "member_count": len(members),
        "project_count": len([item for item in workspace.projects if item.deleted_at is None]),
        "last_active_at": iso(workspace.last_active_at),
        "members": [workspace_member_to_dict(item) for item in members],
        "created_at": iso(workspace.created_at),
        "updated_at": iso(workspace.updated_at),
    }


def project_to_dict(project: Project) -> dict[str, Any]:
    return {
        "id": project.id,
        "project_id": project.id,
        "workspace_id": project.workspace_id,
        "name": project.name,
        "description": project.description,
        "type": project.type,
        "status": project.status,
        "tags": project.tags or [],
        "context": project.context or {},
        "file_count": project.file_count,
        "current_version": project.current_version,
        "created_at": iso(project.created_at),
        "updated_at": iso(project.updated_at),
    }


def project_file_to_dict(file: ProjectFile, include_content: bool = True) -> dict[str, Any]:
    payload = {
        "id": file.id,
        "file_id": file.id,
        "project_id": file.project_id,
        "path": file.path,
        "language": file.language,
        "checksum": file.checksum,
        "size": file.size,
        "version": file.version,
        "created_at": iso(file.created_at),
        "updated_at": iso(file.updated_at),
    }
    if include_content:
        payload["content"] = file.content
    return payload


def prompt_template_to_dict(template: PromptTemplate) -> dict[str, Any]:
    return {
        "id": template.id,
        "name": template.name,
        "description": template.description,
        "scope": template.scope,
        "category": template.category,
        "content": template.content,
        "variables": template.variables or [],
        "version": template.version,
        "status": template.status,
        "workspace_id": template.workspace_id,
        "created_at": iso(template.created_at),
        "updated_at": iso(template.updated_at),
    }


def shortcut_command_to_dict(command: ShortcutCommand) -> dict[str, Any]:
    return {
        "id": command.id,
        "name": command.name,
        "description": command.description,
        "prompt_template": command.prompt_template,
        "agent_route": command.agent_route or {},
        "parameters_schema": command.parameters_schema or {},
        "status": command.status,
        "workspace_id": command.workspace_id,
        "created_at": iso(command.created_at),
        "updated_at": iso(command.updated_at),
    }


def skill_to_dict(skill: Skill) -> dict[str, Any]:
    return {
        "id": skill.id,
        "skill_id": skill.id,
        "workspace_id": skill.workspace_id,
        "name": skill.name,
        "description": skill.description,
        "category": skill.category,
        "source": skill.source,
        "status": skill.status,
        "version": skill.version,
        "content": skill.content,
        "prompt": skill.prompt,
        "input_schema": skill.input_schema or {},
        "output_schema": skill.output_schema or {},
        "tools": redact_sensitive(skill.tools or []),
        "tags": skill.tags or [],
        "config": redact_sensitive(skill.config or {}),
        "metadata": redact_sensitive(skill.extra or {}),
        "created_by": skill.owner_id,
        "created_at": iso(skill.created_at),
        "updated_at": iso(skill.updated_at),
    }


def skill_run_to_dict(run: SkillRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "skill_id": run.skill_id,
        "owner_id": run.owner_id,
        "conversation_id": run.conversation_id,
        "runtime_type": run.runtime_type,
        "status": run.status,
        "input": redact_sensitive(run.input or {}),
        "output": redact_sensitive(run.output or {}),
        "error_message": run.error_message,
        "duration_ms": run.duration_ms,
        "started_at": iso(run.started_at),
        "completed_at": iso(run.completed_at),
        "metadata": redact_sensitive(run.extra or {}),
        "created_at": iso(run.created_at),
        "updated_at": iso(run.updated_at),
    }


def tool_definition_to_dict(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "id": tool.id,
        "tool_id": tool.id,
        "workspace_id": tool.workspace_id,
        "name": tool.name,
        "display_name": tool.display_name or tool.name,
        "description": tool.description,
        "category": tool.category,
        "type": tool.type,
        "status": tool.status,
        "version": tool.version,
        "input_schema": tool.input_schema or {},
        "output_schema": tool.output_schema or {},
        "permissions": tool.permissions or [],
        "implementation": redact_sensitive(tool.implementation or {}),
        "runtime": redact_sensitive(tool.runtime or {}),
        "tags": tool.tags or [],
        "config": redact_sensitive(tool.config or {}),
        "metadata": redact_sensitive(tool.extra or {}),
        "is_builtin": tool.owner_id is None,
        "created_by": tool.owner_id,
        "created_at": iso(tool.created_at),
        "updated_at": iso(tool.updated_at),
    }


def tool_invocation_to_dict(invocation: ToolInvocation) -> dict[str, Any]:
    return {
        "id": invocation.id,
        "tool_id": invocation.tool_id,
        "owner_id": invocation.owner_id,
        "workspace_id": invocation.workspace_id,
        "conversation_id": invocation.conversation_id,
        "tool_name": invocation.tool_name,
        "tool_type": invocation.tool_type,
        "arguments": redact_sensitive(invocation.arguments or {}),
        "result": redact_sensitive(invocation.result or {}),
        "status": invocation.status,
        "error_message": invocation.error_message,
        "duration_ms": invocation.duration_ms,
        "started_at": iso(invocation.started_at),
        "completed_at": iso(invocation.completed_at),
        "metadata": redact_sensitive(invocation.extra or {}),
        "created_at": iso(invocation.created_at),
        "updated_at": iso(invocation.updated_at),
    }


def model_provider_to_dict(provider: ModelProvider, include_secret: bool = False) -> dict[str, Any]:
    return {
        "id": provider.id,
        "name": provider.name,
        "provider_type": provider.provider_type,
        "base_url": provider.base_url,
        "api_key_set": bool(provider.api_key_ref and provider.api_key_ref != "mock"),
        **({"api_key_ref": provider.api_key_ref} if include_secret else {}),
        "default_model": provider.default_model,
        "status": provider.status,
        "supports_streaming": provider.supports_streaming,
        "supports_embeddings": provider.supports_embeddings,
        "config": strip_sensitive(provider.config or {}),
        "model_count": len(provider.models),
        "created_at": iso(provider.created_at),
        "updated_at": iso(provider.updated_at),
    }


def model_config_to_dict(model: ModelConfig) -> dict[str, Any]:
    return {
        "id": model.id,
        "provider_id": model.provider_id,
        "provider_name": model.provider.name if model.provider else None,
        "name": model.name,
        "model_id": model.model_id,
        "purpose": model.purpose,
        "context_window": model.context_window,
        "max_output_tokens": model.max_output_tokens,
        "temperature_default": model.temperature_default,
        "status": model.status,
        "config": strip_sensitive(model.config or {}),
        "created_at": iso(model.created_at),
        "updated_at": iso(model.updated_at),
    }


def mcp_server_to_dict(server: McpServer) -> dict[str, Any]:
    return {
        "id": server.id,
        "workspace_id": server.workspace_id,
        "name": server.name,
        "transport": server.transport,
        "url": server.url,
        "command": server.command,
        "args": server.args or [],
        "enabled": server.enabled,
        "tool_filter": server.tool_filter or [],
        "timeout_ms": server.timeout_ms,
        "retry": server.retry or 1,
        "health_status": server.health_status,
        "last_checked_at": iso(server.last_checked_at),
        "tools": server.tools or [],
        "created_by": server.owner_id,
        "created_at": iso(server.created_at),
        "updated_at": iso(server.updated_at),
    }


def mcp_invocation_to_dict(invocation: McpToolInvocation) -> dict[str, Any]:
    return {
        "id": invocation.id,
        "server_id": invocation.server_id,
        "workspace_id": invocation.workspace_id,
        "conversation_id": invocation.conversation_id,
        "tool_name": invocation.tool_name,
        "transport": invocation.transport,
        "arguments": redact_sensitive(invocation.arguments or {}),
        "status": invocation.status,
        "result": redact_sensitive(invocation.result or {}),
        "error_message": invocation.error_message,
        "error_code": (invocation.extra or {}).get("error_code"),
        "duration_ms": invocation.duration_ms,
        "started_at": iso(invocation.started_at),
        "completed_at": iso(invocation.completed_at),
        "created_at": iso(invocation.created_at),
        "updated_at": iso(invocation.updated_at),
    }


def workflow_run_to_dict(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "conversation_id": getattr(run, "conversation_id", None),
        "trigger_message_id": getattr(run, "trigger_message_id", None),
        "status": getattr(run, "status", ""),
        "mode": getattr(run, "mode", ""),
        "workflow_snapshot": getattr(run, "workflow_snapshot", {}) or {},
        "node_states": getattr(run, "node_states", []) or [],
        "edge_states": getattr(run, "edge_states", []) or [],
        "events": getattr(run, "events", []) or [],
        "progress": getattr(run, "progress", 0),
        "started_at": iso(getattr(run, "started_at", None)),
        "completed_at": iso(getattr(run, "completed_at", None)),
        "created_at": iso(getattr(run, "created_at", None)),
        "updated_at": iso(getattr(run, "updated_at", None)),
    }


def sandbox_to_dict(session: SandboxSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "workspace_id": session.workspace_id,
        "project_id": session.project_id,
        "name": session.name,
        "image": session.image,
        "status": session.status,
        "resource_limits": session.resource_limits or {},
        "mounted_files": session.mounted_files or [],
        "command_history": session.command_history or [],
        "last_command_at": iso(session.last_command_at),
        "expires_at": iso(session.expires_at),
        "created_at": iso(session.created_at),
        "updated_at": iso(session.updated_at),
    }


def remote_connection_to_dict(connection: RemoteConnection) -> dict[str, Any]:
    return {
        "id": connection.id,
        "workspace_id": connection.workspace_id,
        "name": connection.name,
        "connection_type": connection.connection_type,
        "endpoint": connection.endpoint,
        "status": connection.status,
        "capabilities": connection.capabilities or [],
        "session_state": connection.session_state or {},
        "last_connected_at": iso(connection.last_connected_at),
        "created_at": iso(connection.created_at),
        "updated_at": iso(connection.updated_at),
    }
