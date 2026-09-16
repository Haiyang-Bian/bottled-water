"""Host-independent authority and resource interfaces for tool execution."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from .permissions import ExecutionPolicySnapshot


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
    policy: ExecutionPolicySnapshot | None = None
    file_access_scope: Literal["workspace", "user"] = "workspace"

    def __post_init__(self):
        if self.execution_mode not in {"current_user", "windows_lpac"}:
            raise ValueError("Unsupported execution mode")
        if (self.execution_mode == "windows_lpac") != (self.policy is not None):
            raise ValueError("Restricted execution requires a frozen policy")
        if self.file_access_scope not in {"workspace", "user"}:
            raise ValueError("Unsupported file access scope")
        if self.execution_mode == "windows_lpac" and self.file_access_scope != "workspace":
            raise ValueError("Restricted execution cannot use current-user file scope")


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


@dataclass(frozen=True)
class AuthorizationRequest:
    """Validated arguments; target resolution belongs to the selected driver."""

    spec: ToolSpec
    context: ExecutionContext
    parameters: dict
    operation: str | None = None
    target: str | None = None


class AuthorizationPort(Protocol):
    def authorize(
        self, request: AuthorizationRequest
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


class FileOperationsPort(Protocol):
    async def invoke(self, operation: str, parameters: dict, context: ExecutionContext) -> dict: ...


class PermissionLeasePort(Protocol):
    def require_valid(self) -> None: ...
    def finish(self, *, job_drained: bool) -> None: ...


class ExecutionIsolationPort(Protocol):
    capabilities: dict[str, bool]

    async def prepare(self, context: ExecutionContext) -> None: ...
    async def drain(self) -> None: ...
    async def revoke(self, reason_code: str) -> None: ...
    async def aclose(self) -> None: ...


class CredentialStore(Protocol):
    def resolve(self, reference: str) -> str: ...
    def save(self, value: str) -> str: ...
