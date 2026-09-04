"""Environment references and current-user Windows DPAPI credentials."""

import os
import re
from uuid import uuid4

from agent_contracts.errors import ConfigurationError


class LocalCredentialStore:
    def __init__(self, directory):
        self.directory = directory

    def resolve(self, reference):
        kind, _, key = reference.partition(":")
        if kind == "env" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            value = os.environ.get(key, "")
        elif kind == "dpapi" and re.fullmatch(r"[0-9a-f]{32}", key):
            if os.name != "nt":
                raise ConfigurationError("DPAPI credentials require Windows; use an env reference")
            try:
                import win32crypt

                value = win32crypt.CryptUnprotectData(
                    (self.directory / (key + ".bin")).read_bytes(), None, None, None, 1
                )[1].decode("utf-8")
            except Exception as exc:
                raise ConfigurationError("Cannot decrypt the selected credential") from exc
        else:
            raise ConfigurationError("Credential reference must be env:NAME or dpapi:ID")
        if not value:
            raise ConfigurationError("The selected credential is empty or unavailable")
        return value

    def save(self, value):
        if not value:
            raise ConfigurationError("API key cannot be empty")
        if os.name != "nt":
            raise ConfigurationError("Use an environment credential reference on this platform")
        import win32crypt

        self.directory.mkdir(parents=True, exist_ok=True)
        identifier = uuid4().hex
        protected = win32crypt.CryptProtectData(
            value.encode("utf-8"), "AgentHub", None, None, None, 1
        )
        (self.directory / (identifier + ".bin")).write_bytes(protected)
        return "dpapi:" + identifier
