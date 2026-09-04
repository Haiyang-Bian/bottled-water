"""
工具执行器

负责执行工具调用，处理异常，返回结果。

同时支持内置工具和 MCP 工具：
- 内置工具：本地 Python 函数直接调用
- MCP 工具：通过 MCP 执行器调用外部服务
"""

import asyncio

from agent_contracts.logging import get_logger
from .registry import ToolRegistry
from agent_runtime.core.types import ToolCall, ToolResult

logger = get_logger(__name__)


class ToolExecutorImpl:
    """工具执行器实现

    对上层透明地处理两类工具：
    1. 内置工具（builtin）：直接调用本地 handler
    2. MCP 工具（mcp）：通过 MCP 协议调用外部服务
    """

    def __init__(self, registry: ToolRegistry):
        self.registry = registry

    async def execute(self, tool_call: ToolCall) -> ToolResult:
        """执行工具调用"""
        logger.info("工具执行", tool=tool_call.tool_name, call_id=tool_call.call_id)

        tool_info = self.registry.get_tool(tool_call.tool_name)
        if not tool_info:
            logger.warning("工具未找到", tool=tool_call.tool_name)
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=f"Tool not found: {tool_call.tool_name}",
            )

        # 判断工具类型并路由到对应执行器
        if self.registry.is_mcp_tool(tool_call.tool_name):
            return await self._execute_mcp(tool_call, tool_info)
        return await self._execute_builtin(tool_call)

    async def _execute_builtin(self, tool_call: ToolCall) -> ToolResult:
        """执行内置工具"""
        handler = self.registry.get_handler(tool_call.tool_name)
        if not handler:
            logger.warning("内置工具无 handler", tool=tool_call.tool_name)
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=f"Builtin tool handler missing: {tool_call.tool_name}",
            )

        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(**tool_call.parameters)
            else:
                result = handler(**tool_call.parameters)

            logger.info("内置工具执行成功", tool=tool_call.tool_name, call_id=tool_call.call_id)
            return ToolResult(
                call_id=tool_call.call_id,
                success=True,
                result=result,
            )
        except Exception as e:
            logger.error(
                "内置工具执行失败", tool=tool_call.tool_name, call_id=tool_call.call_id, error=str(e)
            )
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=str(e),
            )

    async def _execute_mcp(self, tool_call: ToolCall, tool_info: dict) -> ToolResult:
        """执行 MCP 工具"""
        mcp_executor = self.registry.get_mcp_executor()
        if not mcp_executor:
            logger.error("MCP 执行器未配置", tool=tool_call.tool_name)
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error="MCP executor not configured",
            )

        try:
            server_id = tool_info.get("server_id", "")
            result = await mcp_executor(
                tool_name=tool_call.tool_name,
                parameters=tool_call.parameters,
                server_id=server_id,
            )

            logger.info("MCP 工具执行成功", tool=tool_call.tool_name, server_id=server_id)
            return ToolResult(
                call_id=tool_call.call_id,
                success=True,
                result=result,
            )
        except Exception as e:
            logger.error(
                "MCP 工具执行失败", tool=tool_call.tool_name, server_id=tool_info.get("server_id"), error=str(e)
            )
            return ToolResult(
                call_id=tool_call.call_id,
                success=False,
                result=None,
                error=str(e),
            )

    async def list_tools(self) -> list:
        """列出可用工具"""
        return self.registry.list_tools()
