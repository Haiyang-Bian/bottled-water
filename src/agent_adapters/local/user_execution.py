"""Keep native execution in the ordinary Windows user's token, without a broker."""

import os

from agent_contracts.errors import ConfigurationError


def is_elevated():
    if os.name != "nt":
        return False
    try:
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            return bool(win32security.GetTokenInformation(token, win32security.TokenElevation))
        finally:
            token.Close()
    except Exception as exc:
        raise ConfigurationError("无法核实 Windows 执行令牌；请在普通终端重试。") from exc


def require_ordinary_user():
    if is_elevated():
        raise ConfigurationError(
            "AgentHub 不在管理员令牌下执行任务或工具管理操作。请关闭管理员终端，"
            "在普通终端重新运行；历史、配置和诊断仍可读取。"
        )
