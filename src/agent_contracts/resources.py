"""Local resource knowledge is independent of execution authority and task history."""

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ResourceAccessContext:
    environment_id: str
    agent_id: str = "local"
    scope_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class ResourceRevision:
    name: str
    path: str
    kind: str = "file"
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class ResourceSource:
    kind: str
    run_id: str | None = None
    sequence: int | None = None
    call_id: str | None = None
    operation_id: str | None = None


@dataclass(frozen=True)
class ResourceObservation:
    id: str
    resource_id: str
    path: str
    facts: dict[str, Any]
    source: ResourceSource
    observed_at: str


@dataclass(frozen=True)
class ResourceRecord:
    id: str
    revision: int
    content: ResourceRevision
    status: str = "active"
    observation: ResourceObservation | None = None


@dataclass(frozen=True)
class SoftwareSpec:
    resource_id: str
    kind: str
    executable: str
    sha256: str
    version: str
    verified_at: str
    enabled: bool = False


@dataclass(frozen=True)
class ResourceContextItem:
    resource_id: str
    revision: int
    observation_id: str | None
    text: str


@dataclass(frozen=True)
class ResourceLink:
    resource_id: str
    run_id: str
    sequence: int
    relation: str


@dataclass(frozen=True)
class TaskReference:
    id: str
    title: str
    cwd: str
    last_active: str
    state: str
    runs: tuple[dict, ...] = ()
    resources: tuple[ResourceLink, ...] = ()
    diagnostics: dict = field(default_factory=dict)


class ResourceReader(Protocol):
    def search(self, access: ResourceAccessContext, query: str = "", **options): ...
    def read(self, access: ResourceAccessContext, identifier: str, **options): ...


class ResourceWriter(Protocol):
    def save(self, access: ResourceAccessContext, content: ResourceRevision): ...
    def revise(self, access, identifier, revision, content): ...
    def observe(self, access, path, facts, source): ...


class ResourceProcessor(Protocol):
    def process(self, access: ResourceAccessContext, *, max_runs=20, max_events=1000): ...
