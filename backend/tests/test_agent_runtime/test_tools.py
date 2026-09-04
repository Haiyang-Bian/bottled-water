"""
测试工具注册表和执行器
"""

import pytest

from agent_subsystems.tools.registry import ToolRegistry
from agent_subsystems.tools.executor import ToolExecutorImpl
from agent_runtime.core.types import ToolCall, ToolResult


class TestToolRegistry:
    """测试 ToolRegistry"""

    @pytest.fixture
    def registry(self):
        return ToolRegistry()

    def test_register(self, registry):
        def handler(path: str) -> str:
            return f"content of {path}"

        registry.register(
            name="file_read",
            description="读取文件内容",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            handler=handler,
        )

        tool = registry.get_tool("file_read")
        assert tool is not None
        assert tool["name"] == "file_read"
        assert tool["description"] == "读取文件内容"

        handler_fn = registry.get_handler("file_read")
        assert handler_fn is not None
        assert handler_fn("/tmp/test.txt") == "content of /tmp/test.txt"

    def test_list_tools(self, registry):
        registry.register("tool1", "工具1", {}, lambda: None)
        registry.register("tool2", "工具2", {}, lambda: None)

        tools = registry.list_tools()
        assert len(tools) == 2
        assert all(t["type"] == "function" for t in tools)
        assert tools[0]["function"]["name"] == "tool1"
        assert tools[1]["function"]["name"] == "tool2"

    def test_get_tool_not_found(self, registry):
        assert registry.get_tool("nonexistent") is None
        assert registry.get_handler("nonexistent") is None

    def test_unregister(self, registry):
        registry.register("tool1", "工具1", {}, lambda: None)
        assert registry.get_tool("tool1") is not None

        registry.unregister("tool1")
        assert registry.get_tool("tool1") is None
        assert registry.get_handler("tool1") is None


class TestToolExecutorImpl:
    """测试 ToolExecutorImpl"""

    @pytest.fixture
    def registry(self):
        reg = ToolRegistry()
        reg.register(
            "add",
            "加法",
            {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            lambda a, b: a + b,
        )
        reg.register(
            "async_add",
            "异步加法",
            {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
            lambda a, b: a + b,  # 同步版本用于测试
        )
        return reg

    @pytest.fixture
    def executor(self, registry):
        return ToolExecutorImpl(registry)

    @pytest.mark.asyncio
    async def test_execute_sync_tool(self, executor):
        call = ToolCall(tool_name="add", parameters={"a": 1, "b": 2}, call_id="call_1")
        result = await executor.execute(call)
        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.result == 3
        assert result.call_id == "call_1"
        assert result.error is None

    @pytest.mark.asyncio
    async def test_execute_not_found(self, executor):
        call = ToolCall(tool_name="nonexistent", parameters={}, call_id="call_1")
        result = await executor.execute(call)
        assert result.success is False
        assert result.error == "Tool not found: nonexistent"

    @pytest.mark.asyncio
    async def test_execute_error(self, executor):
        # 注册一个会抛出异常的工具
        def bad_tool():
            raise ValueError("出错了")

        executor.registry.register("bad", "坏工具", {}, bad_tool)
        call = ToolCall(tool_name="bad", parameters={}, call_id="call_1")
        result = await executor.execute(call)
        assert result.success is False
        assert "出错了" in result.error

    @pytest.mark.asyncio
    async def test_list_tools(self, executor):
        tools = await executor.list_tools()
        assert len(tools) == 2  # add, async_add


class TestMcpToolIntegration:
    """测试 MCP 工具统一接入"""

    @pytest.fixture
    def registry_with_mcp(self):
        """同时包含内置工具和 MCP 工具的注册表"""
        reg = ToolRegistry()
        # 内置工具
        reg.register(
            "file_read",
            "读取文件",
            {"type": "object", "properties": {"path": {"type": "string"}}},
            lambda path: f"content of {path}",
        )
        # MCP 工具
        reg.register_mcp(
            "weather.query",
            "查询天气",
            {"type": "object", "properties": {"city": {"type": "string"}}},
            server_id="weather-server",
        )
        return reg

    @pytest.fixture
    def mcp_executor_mock(self):
        """MCP 执行器 mock"""
        async def mock_executor(tool_name, parameters, server_id):
            return {"temperature": 25, "city": parameters.get("city")}
        return mock_executor

    def test_mcp_tool_registration(self, registry_with_mcp):
        """测试 MCP 工具注册"""
        tool = registry_with_mcp.get_tool("weather.query")
        assert tool is not None
        assert tool["name"] == "weather.query"
        assert tool["source"] == "mcp"
        assert tool["server_id"] == "weather-server"

        assert registry_with_mcp.is_mcp_tool("weather.query") is True
        assert registry_with_mcp.is_mcp_tool("file_read") is False

    def test_mcp_tool_listing(self, registry_with_mcp):
        """测试 MCP 工具在 list_tools 中统一列出"""
        tools = registry_with_mcp.list_tools()
        names = [t["function"]["name"] for t in tools]
        assert "file_read" in names
        assert "weather.query" in names
        assert len(tools) == 2

    def test_tool_source_lists(self, registry_with_mcp):
        """测试按来源列出工具"""
        assert registry_with_mcp.list_builtin_tools() == ["file_read"]
        assert registry_with_mcp.list_mcp_tools() == ["weather.query"]

    @pytest.mark.asyncio
    async def test_mcp_tool_execution(self, registry_with_mcp, mcp_executor_mock):
        """测试 MCP 工具执行"""
        registry_with_mcp.set_mcp_executor(mcp_executor_mock)
        executor = ToolExecutorImpl(registry_with_mcp)

        call = ToolCall(tool_name="weather.query", parameters={"city": "北京"}, call_id="call_1")
        result = await executor.execute(call)

        assert isinstance(result, ToolResult)
        assert result.success is True
        assert result.result["temperature"] == 25
        assert result.result["city"] == "北京"

    @pytest.mark.asyncio
    async def test_mcp_executor_not_configured(self, registry_with_mcp):
        """测试 MCP 执行器未配置时的错误"""
        executor = ToolExecutorImpl(registry_with_mcp)

        call = ToolCall(tool_name="weather.query", parameters={"city": "北京"}, call_id="call_1")
        result = await executor.execute(call)

        assert result.success is False
        assert "MCP executor not configured" in result.error

    @pytest.mark.asyncio
    async def test_builtin_and_mcp_mixed(self, registry_with_mcp, mcp_executor_mock):
        """测试内置和 MCP 工具混合使用"""
        registry_with_mcp.set_mcp_executor(mcp_executor_mock)
        executor = ToolExecutorImpl(registry_with_mcp)

        # 执行内置工具
        builtin_call = ToolCall(tool_name="file_read", parameters={"path": "/tmp/test.txt"}, call_id="call_1")
        builtin_result = await executor.execute(builtin_call)
        assert builtin_result.success is True
        assert "content of /tmp/test.txt" in builtin_result.result

        # 执行 MCP 工具
        mcp_call = ToolCall(tool_name="weather.query", parameters={"city": "上海"}, call_id="call_2")
        mcp_result = await executor.execute(mcp_call)
        assert mcp_result.success is True
        assert mcp_result.result["city"] == "上海"
