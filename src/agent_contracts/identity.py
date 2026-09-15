"""Stable local identities, independent of models and working directories."""

from dataclasses import dataclass


@dataclass(frozen=True)
class LocalEnvironment:
    environment_id: str
    default_agent_id: str = "local"


@dataclass(frozen=True)
class PlatformIdentity:
    owner_key: str
    machine_key: str
    binding_kind: str
