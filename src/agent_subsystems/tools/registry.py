"""
工具注册表

管理可用工具的注册和查询。
同时支持内置工具和 MCP 工具的统一纳管。
"""

from typing import Dict, List, Any, Callable, Optional


class ToolRegistry:
    """工具注册表

    同时纳管两类工具：
    - 内置工具（Builtin）：本地 Python 函数，直接执行
    - MCP 工具（MCP）：通过 MCP 协议调用外部服务

    对上层（LLM、Agent）透明，统一通过 list_tools() 发现和 execute() 调用。
    """

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}
        self._handlers: Dict[str, Callable] = {}
        # MCP 工具执行器（由外部注入，避免 agent_runtime 依赖 app 层）
        self._mcp_executor: Optional[Callable] = None

    # --- 内置工具 ---

    def register(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        handler: Callable,
    ):
        """注册内置工具"""
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "source": "builtin",
        }
        self._handlers[name] = handler

    # --- MCP 工具 ---

    def register_mcp(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        server_id: str,
    ):
        """注册 MCP 工具

        Args:
            name: 工具名称（LLM 可见）
            description: 工具描述
            parameters: 参数 schema
            server_id: MCP 服务器标识，用于路由到正确的 MCP 服务
        """
        self._tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "source": "mcp",
            "server_id": server_id,
        }
        # MCP 工具没有本地 handler，通过 _mcp_executor 执行
        self._handlers[name] = None

    def set_mcp_executor(self, executor: Callable):
        """设置 MCP 工具执行器

        executor 签名：async fn(tool_name: str, parameters: dict, server_id: str) -> Any
        """
        self._mcp_executor = executor

    # --- 通用查询 ---

    def get_tool(self, name: str) -> Optional[Dict[str, Any]]:
        """获取工具定义"""
        return self._tools.get(name)

    def get_handler(self, name: str) -> Optional[Callable]:
        """获取内置工具处理器"""
        return self._handlers.get(name)

    def get_mcp_executor(self) -> Optional[Callable]:
        """获取 MCP 工具执行器"""
        return self._mcp_executor

    def is_mcp_tool(self, name: str) -> bool:
        """判断工具是否为 MCP 工具"""
        info = self._tools.get(name)
        return info is not None and info.get("source") == "mcp"

    def list_tools(self) -> List[Dict[str, Any]]:
        """列出所有工具（用于传递给 LLM）

        内置工具和 MCP 工具格式统一，LLM 无感知差异。
        """
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": info["description"],
                    "parameters": info["parameters"],
                },
            }
            for name, info in self._tools.items()
        ]

    def list_builtin_tools(self) -> List[str]:
        """列出所有内置工具名称"""
        return [name for name, info in self._tools.items() if info.get("source") == "builtin"]

    def list_mcp_tools(self) -> List[str]:
        """列出所有 MCP 工具名称"""
        return [name for name, info in self._tools.items() if info.get("source") == "mcp"]

    def unregister(self, name: str):
        """注销工具"""
        self._tools.pop(name, None)
        self._handlers.pop(name, None)
