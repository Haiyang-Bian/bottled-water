"""Host-bound foundational memory; independent of task context and file grants."""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class MemoryAccessContext:
    environment_id: str
    agent_id: str = "local"
    scope_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True)
class MemorySource:
    kind: str = "user"
    run_id: str | None = None
    sequence: int | None = None
    call_id: str | None = None
    path: str | None = None
    sha256: str | None = None
    memory_id: str | None = None
    revision: int | None = None
    incomplete: bool = False


@dataclass(frozen=True)
class MemoryRevision:
    title: str
    body: str
    kind: str = "preference"
    evidence: str = "user_stated"
    tags: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    directory: str | None = None
    basic: bool = False


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    revision: int
    content: MemoryRevision
    sources: tuple[MemorySource, ...] = ()
    status: str = "active"
    approved: bool = True
    verified_at: str = ""


@dataclass(frozen=True)
class MemoryCandidate:
    id: str
    revision: int
    status: str
    content: MemoryRevision
    sources: tuple[MemorySource, ...]
    reason: str | None = None
    memory_id: str | None = None


@dataclass(frozen=True)
class MemoryContextItem:
    memory_id: str
    revision: int
    text: str
    basic: bool = False


@dataclass
class MemorySelection:
    items: list[MemoryContextItem] = field(default_factory=list)
    diagnostics: dict = field(default_factory=dict)


class MemoryReader(Protocol):
    def search(
        self, access: MemoryAccessContext, query: str = "", **kwargs
    ) -> list[MemoryRecord]: ...
    def read(self, access: MemoryAccessContext, memory_id: str) -> MemoryRecord: ...
    def revalidate(
        self, access: MemoryAccessContext, items: list[MemoryContextItem]
    ) -> list[MemoryContextItem]: ...


class MemoryWriter(Protocol):
    def save(
        self, access: MemoryAccessContext, content: MemoryRevision, **kwargs
    ) -> MemoryRecord: ...
    def revise(
        self, access: MemoryAccessContext, memory_id: str, revision: int, content: MemoryRevision
    ) -> MemoryRecord: ...


class MemoryProcessor(Protocol):
    def process(self, access: MemoryAccessContext, limit: int = 20) -> dict: ...
