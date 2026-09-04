"""Global CLI configuration; project directories cannot override authority."""

import os
import math
import tomllib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from agent_contracts.errors import ConfigurationError


def home_directory():
    return (
        Path(os.environ.get("AGENTHUB_HOME", str(Path.home() / ".agenthub"))).expanduser().resolve()
    )


@dataclass(frozen=True)
class Profile:
    provider: str
    model: str
    credential_ref: str
    base_url: str = ""
    max_tokens: int = 4096
    timeout_seconds: float = 120
    max_history_chars: int = 64000

    def __post_init__(self):
        if self.provider not in {"openai_compatible", "deepseek"}:
            raise ConfigurationError("Supported CLI providers: openai_compatible, deepseek")
        if not self.model or not self.credential_ref:
            raise ConfigurationError("Model and credential_ref are required")
        if self.provider == "openai_compatible" and not self.base_url:
            raise ConfigurationError("OpenAI-compatible profiles require base_url")
        if self.base_url:
            url = urlsplit(self.base_url)
            if (
                url.scheme not in {"http", "https"}
                or not url.hostname
                or url.username
                or url.password
            ):
                raise ConfigurationError(
                    "base_url must be an HTTP(S) URL without embedded credentials"
                )
        if any(
            not math.isfinite(value) or value <= 0
            for value in (self.max_tokens, self.timeout_seconds, self.max_history_chars)
        ):
            raise ConfigurationError("Profile limits must be positive")


def load_config(directory):
    try:
        with (directory / "config.toml").open("rb") as stream:
            return tomllib.load(stream)
    except FileNotFoundError as exc:
        raise ConfigurationError("Run 'agenthub init' to configure a model first") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError("Invalid config.toml") from exc


def select_profile(config, name=None):
    name = name or config.get("default_profile", "default")
    try:
        return name, Profile(**config["profiles"][name])
    except (KeyError, TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid or missing profile: {name}") from exc


def save_config(directory, config):
    import tomli_w

    directory.mkdir(parents=True, exist_ok=True)
    temporary = directory / ("config-" + uuid4().hex + ".tmp")
    try:
        temporary.write_text(tomli_w.dumps(config), encoding="utf-8")
        temporary.replace(directory / "config.toml")
    finally:
        temporary.unlink(missing_ok=True)
