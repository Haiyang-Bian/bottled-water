from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.core.errors import ValidationAppError
from app.models import Conversation, McpServer, Skill, ToolDefinition, User
from app.services.tools.builtins.executor import invoke_builtin_tool
from app.services.tools.catalog import ensure_tool_tables, get_tool_definition
from app.services.tools.custom import invoke_custom_tool
from app.services.tools.permissions import canonical_tool_name, check_user_tool_permissions
from app.services.tools.runs import finish_tool_invocation, start_tool_invocation
from app.services.tools.schema import validate_tool_arguments
from app.services.tools.execution_root import TrustedExecutionRoot


def invoke_tool(
    db: Session,
    user: User,
    tool_id_or_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    """统一执行内置工具和自定义工具。"""

    ensure_tool_tables(db)
    original_tool_name = str(tool_id_or_name)
    tool_id_or_name = canonical_tool_name(tool_id_or_name)
    arguments = _legacy_alias_arguments(original_tool_name, tool_id_or_name, arguments)
    execution_root = arguments.pop("_trusted_execution_root", None)
    if not isinstance(execution_root, TrustedExecutionRoot):
        execution_root = None
    conversation_id = str(arguments.get("conversation_id") or "") or None
    tool = get_tool_definition(db, user, tool_id_or_name)
    validate_tool_arguments(tool.input_schema, arguments, tool_name=tool.name)
    permission_warnings = check_user_tool_permissions(user, tool.permissions or [], strict=False)
    invocation, started = start_tool_invocation(
        db,
        user=user,
        tool_name=tool.name,
        tool_type=tool.type,
        arguments=arguments,
        tool=tool,
        conversation_id=conversation_id,
    )
    try:
        dispatch_arguments = dict(arguments)
        if execution_root is not None:
            dispatch_arguments["_trusted_execution_root"] = execution_root
        result = _dispatch_tool(db, user, tool, dispatch_arguments, conversation_id)
        status = result.get("status") or result.get("result", {}).get("status") or "succeeded"
        finish_tool_invocation(invocation, started, status=status, result=result)
    except Exception as exc:
        finish_tool_invocation(invocation, started, status="failed", error=str(exc), result={"error": str(exc)})
        raise
    payload = _executor_payload(tool, result, invocation.id)
    if permission_warnings:
        payload["permission_warnings"] = permission_warnings
    return payload


async def invoke_tool_async(
    db: Session,
    user: User,
    tool_id_or_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    ensure_tool_tables(db)
    original_tool_name = str(tool_id_or_name)
    tool_id_or_name = canonical_tool_name(tool_id_or_name)
    arguments = _legacy_alias_arguments(original_tool_name, tool_id_or_name, arguments)
    conversation_id = str(arguments.get("conversation_id") or "") or None
    tool = get_tool_definition(db, user, tool_id_or_name)
    validate_tool_arguments(tool.input_schema, arguments, tool_name=tool.name)
    permission_warnings = check_user_tool_permissions(user, tool.permissions or [], strict=False)
    invocation, started = start_tool_invocation(
        db,
        user=user,
        tool_name=tool.name,
        tool_type=tool.type,
        arguments=arguments,
        tool=tool,
        conversation_id=conversation_id,
    )
    try:
        result = await _dispatch_tool_async(db, user, tool, arguments, conversation_id)
        status = result.get("status") or result.get("result", {}).get("status") or "succeeded"
        finish_tool_invocation(invocation, started, status=status, result=result)
    except Exception as exc:
        finish_tool_invocation(invocation, started, status="failed", error=str(exc), result={"error": str(exc)})
        raise
    payload = _executor_payload(tool, result, invocation.id)
    if permission_warnings:
        payload["permission_warnings"] = permission_warnings
    return payload


def _dispatch_tool(
    db: Session,
    user: User,
    tool: ToolDefinition,
    arguments: dict[str, Any],
    conversation_id: str | None,
) -> dict[str, Any]:
    if tool.is_builtin or tool.type == "builtin":
        handler = tool.builtin_handler or tool.name
        return invoke_builtin_tool(db, user, handler, arguments)
    if tool.type == "custom_python":
        return invoke_custom_tool(db, user, tool, arguments)
    if tool.type == "mcp":
        raise ValidationAppError("MCP tool definitions must be invoked through async dispatcher")
    if tool.type == "skill":
        raise ValidationAppError("Skill tool definitions must be invoked through async dispatcher")
    return invoke_custom_tool(db, user, tool, arguments)


async def _dispatch_tool_async(
    db: Session,
    user: User,
    tool: ToolDefinition,
    arguments: dict[str, Any],
    conversation_id: str | None,
) -> dict[str, Any]:
    if tool.type in {"builtin", "custom_python"} or tool.is_builtin:
        return _dispatch_tool(db, user, tool, arguments, conversation_id)
    if tool.type == "mcp":
        return await _invoke_mcp_tool_definition(db, user, tool, arguments, conversation_id)
    if tool.type == "skill":
        return await _invoke_skill_tool_definition(db, user, tool, arguments, conversation_id)
    return _dispatch_tool(db, user, tool, arguments, conversation_id)


async def _invoke_mcp_tool_definition(
    db: Session,
    user: User,
    tool: ToolDefinition,
    arguments: dict[str, Any],
    conversation_id: str | None,
) -> dict[str, Any]:
    from app.services.mcp.invocation import invoke_mcp_tool_recorded

    config = {**(tool.implementation or {}), **(tool.config or {})}
    server_id = str(config.get("server_id") or "")
    mcp_tool_name = str(config.get("tool_name") or tool.builtin_handler or tool.name)
    server = db.get(McpServer, server_id)
    if not server or server.deleted_at is not None:
        from app.core.errors import NotFoundError

        raise NotFoundError("MCP server not found for tool definition")
    return await invoke_mcp_tool_recorded(
        db,
        server=server,
        tool_name_value=mcp_tool_name,
        arguments=arguments,
        user=user,
        conversation_id=conversation_id,
        timeout_ms=server.timeout_ms or 30000,
    )


async def _invoke_skill_tool_definition(
    db: Session,
    user: User,
    tool: ToolDefinition,
    arguments: dict[str, Any],
    conversation_id: str | None,
) -> dict[str, Any]:
    from app.core.errors import NotFoundError
    from app.services.skills.runtime import SkillRuntime

    config = {**(tool.implementation or {}), **(tool.config or {})}
    skill_id = str(config.get("skill_id") or "")
    skill = db.get(Skill, skill_id)
    if not skill or skill.deleted_at is not None:
        raise NotFoundError("Skill not found for tool definition")
    conversation = db.get(Conversation, conversation_id) if conversation_id else None
    return await SkillRuntime().run(
        db,
        skill=skill,
        user=user,
        conversation=conversation,
        payload=arguments,
    )


def _tool_catalog_payload(tool: ToolDefinition) -> dict[str, Any]:
    return {
        "id": tool.id,
        "tool_id": tool.id,
        "name": tool.name,
        "display_name": tool.display_name or tool.name,
        "category": tool.category,
        "description": tool.description,
        "type": tool.type,
        "is_builtin": bool(tool.is_builtin),
        "builtin_handler": tool.builtin_handler,
        "permissions": tool.permissions or [],
        "input_schema": tool.input_schema or {},
        "output_schema": tool.output_schema or {},
        "status": tool.status,
    }


def _executor_payload(tool: ToolDefinition, result: dict[str, Any], invocation_id: str) -> dict[str, Any]:
    if tool.type == "custom_python" and "tool" in result and "result" in result:
        return {**result, "invocation_id": invocation_id}
    return {"tool": _tool_catalog_payload(tool), "result": result, "invocation_id": invocation_id}


def _legacy_alias_arguments(
    original_tool_name: str,
    canonical_name: str,
    arguments: dict[str, Any],
) -> dict[str, Any]:
    if original_tool_name == canonical_name:
        return arguments
    return {**arguments, "_legacy_tool_name": original_tool_name}


__all__ = ["get_tool_definition", "invoke_builtin_tool", "invoke_tool", "invoke_tool_async"]
