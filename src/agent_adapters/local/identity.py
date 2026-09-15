"""Read local binding identifiers. This is not an OS security boundary."""

import hashlib
import os
import platform

from agent_contracts.errors import ConfigurationError
from agent_contracts.identity import PlatformIdentity


def current_identity():
    try:
        if os.name == "nt":
            import winreg
            import win32api
            import win32con
            import win32security

            token = win32security.OpenProcessToken(
                win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
            )
            try:
                sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
                owner = win32security.ConvertSidToStringSid(sid)
            finally:
                token.Close()
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography",
                0, winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
            ) as key:
                machine = winreg.QueryValueEx(key, "MachineGuid")[0]
            kind = "windows_sid_machine"
        else:
            owner, machine, kind = str(os.getuid()), platform.node(), "uid_hostname_advisory"
        if not owner or not machine:
            raise ValueError("Missing platform identity")
        return PlatformIdentity(
            hashlib.sha256((kind + ":" + owner).encode()).hexdigest(),
            hashlib.sha256((kind + ":" + machine).encode()).hexdigest(), kind,
        )
    except Exception as exc:
        raise ConfigurationError("Cannot establish the local environment identity") from exc
