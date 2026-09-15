"""Host-independent authority and resource interfaces for tool execution."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol


@dataclass(frozen=True)
class WorkspaceSpec:
    roots: tuple[Path, ...]


@dataclass(frozen=True)
class ExecutionLocation:
    cwd: Path
    version: int = 0


@dataclass(frozen=True)
class ResourceGrant:
    workspace: WorkspaceSpec
    capabilities: frozenset[str]
    execution_mode: str = "current_user"


@dataclass(frozen=True)
class ExecutionContext:
    run_id: str
    scope_id: str
    agent_id: str
    grant: ResourceGrant
    deadline: float
    cancellation: Any
    lease: Any
    location: ExecutionLocation

    def check(self):
        self.cancellation.raise_if_cancelled()
        self.lease.require_valid()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict
    capability: str


class AuthorizationPort(Protocol):
    def authorize(
        self, spec: ToolSpec, context: ExecutionContext
    ) -> Literal["allow", "deny", "requires_user"]: ...


class ToolInvoker(Protocol):
    async def execute(self, tool_call): ...
    async def list_tools(self) -> list[dict]: ...


class ProcessDriver(Protocol):
    capabilities: dict[str, bool]

    async def run(
        self,
        argv: list[str],
        cwd: Path,
        *,
        timeout: float,
        context: ExecutionContext,
        env: dict[str, str] | None = None,
    ) -> dict: ...
    async def aclose(self) -> None: ...


class CredentialStore(Protocol):
    def resolve(self, reference: str) -> str: ...
    def save(self, value: str) -> str: ...
