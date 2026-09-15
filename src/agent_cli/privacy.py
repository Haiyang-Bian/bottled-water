"""Resolve available configured secrets for local display, without a model client."""

from agent_contracts.errors import ConfigurationError
from agent_subsystems.observability.redaction import Redactor


def display_redactor(home):
    from agent_adapters.credentials.local import LocalCredentialStore
    from .config import load_config
    secrets = []
    try:
        config = load_config(home)
    except ConfigurationError:
        return Redactor()
    credentials = LocalCredentialStore(home / "credentials")
    for profile in config.get("profiles", {}).values():
        try:
            secrets.append(credentials.resolve(profile.get("credential_ref", "")))
        except (ConfigurationError, OSError, ValueError):
            pass  # Unavailable credentials must not prevent browsing saved records.
    return Redactor(secrets)
