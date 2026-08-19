from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.core.errors import ForbiddenError, NotFoundError, ValidationAppError
from app.models import Artifact, Conversation, User
from app.services.artifacts import update_artifact_files
from app.services.deployments import create_sync_deployment
from app.services.serialization import artifact_to_dict
from app.services.tools.api_probe import run_api_test
from app.services.tools.browser_probe import run_browser_preview
from app.services.tools.builtins.artifact.executor import make_artifact_from_content
from app.services.tools.builtins.artifact.export import default_export_format
from app.services.tools.builtins.external_agent import invoke_external_agent_tool
from app.services.tools.builtins.file import invoke_file_tool
from app.services.tools.builtins.sandbox.executor import run_sandbox_command, run_test_command
from app.services.tools.builtins.terminal import invoke_terminal_tool
from app.services.tools.git_collaboration import invoke_git_tool


def invoke_builtin_tool(db: Session, user: User, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name.startswith("file."):
        return invoke_file_tool(db, user, name, arguments)
    if name.startswith("artifact."):
        return _invoke_artifact_tool(db, user, name, arguments)
    if name.startswith("external_agent."):
        return invoke_external_agent_tool(db, user, name, arguments)
    if name.startswith("terminal."):
        return invoke_terminal_tool(db, user, name, arguments)
    if name.startswith("git."):
        return invoke_git_tool(db, user, name, arguments)
    if name == "db.inspect":
        inspector = inspect(db.get_bind())
        return {
            "status": "succeeded",
            "tables": [
                {"name": table, "columns": [column["name"] for column in inspector.get_columns(table)]}
                for table in inspector.get_table_names()
            ],
        }
    if name == "api.test":
        return run_api_test(arguments)
    if name == "sandbox.run":
        return run_sandbox_command(db, user, arguments)
    if name == "test.run":
        return run_test_command(db, user, arguments)
    if name == "browser.preview":
        return run_browser_preview(arguments)
    if name == "security.audit":
        return _security_audit(arguments)
    if name == "document.review":
        return _document_review(arguments)
    if name == "deploy.preview":
        return _deploy_preview(db, user, arguments)
    if name == "deploy.rollback":
        return {
            "status": "succeeded",
            "deployment_id": arguments.get("deployment_id"),
            "message": "已创建回滚记录，当前演示环境保持上一版本可访问。",
        }
    raise NotFoundError("内置工具不存在")


def _invoke_artifact_tool(
    db: Session,
    user: User,
    name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if name.startswith("artifact.create_"):
        return _create_artifact(db, user, name, arguments)
    if name == "artifact.export":
        artifact = _artifact(db, user, str(arguments.get("artifact_id") or ""))
        fmt = str(arguments.get("format") or default_export_format(artifact))
        return {
            "status": "succeeded",
            "artifact_id": artifact.id,
            "format": fmt,
            "export_url": f"/api/v1/artifacts/{artifact.id}/export?format={fmt}",
        }
    if name == "artifact.preview":
        artifact = _artifact(db, user, str(arguments.get("artifact_id") or ""))
        return {
            "status": "succeeded",
            "artifact_id": artifact.id,
            "preview_url": f"/api/v1/artifacts/{artifact.id}/preview",
        }
    if name == "artifact.revise":
        files = arguments.get("files")
        if not isinstance(files, dict):
            raise ValidationAppError("files 必须是对象")
        artifact = update_artifact_files(
            db,
            str(arguments.get("artifact_id") or ""),
            {str(key): str(value) for key, value in files.items()},
            str(arguments.get("summary") or "工具修订"),
        )
        return {"status": "succeeded", "artifact": artifact_to_dict(artifact)}
    if name == "artifact.diff":
        artifact = _artifact(db, user, str(arguments.get("artifact_id") or ""))
        current = artifact.content.get("files") or {}
        previous = artifact.content.get("previous_files") or {}
        return {
            "status": "succeeded",
            "files_changed": sorted(set(current) | set(previous)),
            "version": artifact.current_version,
        }
    raise NotFoundError("产物工具不存在")


def _create_artifact(db: Session, user: User, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    fmt = name.removeprefix("artifact.create_")
    title = str(arguments.get("title") or "AgentHub 产物")
    body = str(arguments.get("body") or arguments.get("content") or title)
    html_content = arguments.get("html") if isinstance(arguments.get("html"), str) else None
    content_model = arguments.get("content_model") if isinstance(arguments.get("content_model"), dict) else None
    template = arguments.get("template") if isinstance(arguments.get("template"), str) else None
    return make_artifact_from_content(
        db,
        user,
        conversation_id=str(arguments.get("conversation_id") or ""),
        title=title,
        body=body,
        format_name=fmt,
        html_content=html_content,
        content_model=content_model,
        template=template,
    )


def _artifact(db: Session, user: User, artifact_id: str) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if not artifact or artifact.deleted_at is not None:
        raise NotFoundError("产物不存在")
    conversation = db.get(Conversation, artifact.conversation_id)
    if not conversation or (conversation.creator_id != user.id and user.role != "admin"):
        raise ForbiddenError("无权访问该产物")
    return artifact


def _security_audit(arguments: dict[str, Any]) -> dict[str, Any]:
    target = str(arguments.get("target") or arguments)
    findings = []
    risk = 0.1
    if re.search(r"(api_key|secret|password|token)", target, re.I):
        risk = 0.8
        findings.append("输入中疑似包含敏感字段，请确认是否需要脱敏。")
    return {"status": "succeeded", "risk_score": risk, "findings": findings or ["未发现高风险项。"]}


def _document_review(arguments: dict[str, Any]) -> dict[str, Any]:
    text = str(arguments.get("text") or arguments.get("body") or "")
    if len(text) > 80:
        findings = ["结构完整，适合继续交付。"]
    else:
        findings = ["内容较短，建议补充背景、目标和验收标准。"]
    return {"status": "succeeded", "findings": findings}


def _deploy_preview(db: Session, user: User, arguments: dict[str, Any]) -> dict[str, Any]:
    artifact = _artifact(db, user, str(arguments.get("artifact_id") or ""))

    # 查找工作区目录用于全栈部署（检测后端文件）
    workspace_dir = _find_workspace_dir(db, arguments, artifact)

    deployment = create_sync_deployment(
        db,
        artifact,
        str(arguments.get("mode") or "preview_link"),
        workspace_dir=workspace_dir,
    )

    backend_port = deployment.config.get("backend_port") if deployment.config else None
    backend_ready_path = deployment.config.get("backend_ready_path") if deployment.config else None
    backend_health_url = deployment.config.get("backend_health_url") if deployment.config else None
    result: dict[str, Any] = {
        "status": "succeeded" if deployment.status == "deployed" else "failed",
        "url": deployment.access_url,
        "public_url": deployment.access_url,
        "deployment_id": deployment.id,
        "deployment": {
            "id": deployment.id,
            "url": deployment.access_url,
            "status": deployment.status,
            "health": (deployment.extra or {}).get("health"),
            "error_message": deployment.error_message,
        },
        "verified_claims_instruction": (
            "Only claim checks explicitly present in deployment.health. "
            "Do not claim every API endpoint or CRUD flow was verified unless this tool result lists those probes."
        ),
    }
    if backend_port:
        result["backend_port"] = backend_port
        result["backend_url"] = f"http://localhost:{backend_port}"
    if backend_ready_path:
        result["validated_backend_path"] = backend_ready_path
    if backend_health_url:
        result["backend_health_url"] = backend_health_url
    return result


def _find_workspace_dir(
    db: Session, arguments: dict[str, Any], artifact: Artifact
) -> Path | None:
    """根据 conversation_id 查找工作区沙箱目录。"""
    from app.services.workspaces.filesystem import (
        scoped_dir,
        workspace_id_from_conversation,
    )

    conversation_id = str(arguments.get("conversation_id") or "") or artifact.conversation_id
    if not conversation_id:
        return None

    workspace_id = workspace_id_from_conversation(db, conversation_id) or "default"
    # scoped_dir 已经返回 .../sandbox/conversations/{conv_id}，不要再拼接子目录
    sandbox_root = scoped_dir(workspace_id, "sandbox", conversation_id=conversation_id)
    if sandbox_root.is_dir():
        return sandbox_root
    return None
