"""Explicit host assembly for acceptance; no CLI permission activation in P2."""

from dataclasses import dataclass

from agent_contracts.execution import ExecutionLocation, ResourceGrant


@dataclass
class ExecutionBindings:
    grant: ResourceGrant
    location: ExecutionLocation
    driver: object
    file_operations: object
    authorization: object
    software: object
    executables: dict[str, str]
    metadata: dict
