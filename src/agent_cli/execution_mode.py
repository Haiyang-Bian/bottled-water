"""Release-facing capability state, separate from retained isolation experiments."""

from agent_contracts.errors import ConfigurationError

NATIVE_LABEL = "普通用户执行 · 跨目录访问 · 网络可用"


def require_available_mode(mode, session_id=None):
    if mode == "windows_lpac":
        resume = f" --resume {session_id}" if session_id else ""
        raise ConfigurationError(
            "Windows LPAC 受限执行暂缓，未自动切换为普通用户模式。历史仍可只读查看。"
            f"如需主动转换：agenthub{resume} --sandbox current-user。"
            "转换后目录和网络按普通用户权限访问。"
        )


def paused_management():
    raise ConfigurationError(
        "受限执行初始化和权限启用暂缓；普通任务不需要 sandbox setup 或 permissions grant。"
        "permissions list 和 sandbox doctor 可只读查看历史配置，旧隔离记录不会自动删除。"
    )
