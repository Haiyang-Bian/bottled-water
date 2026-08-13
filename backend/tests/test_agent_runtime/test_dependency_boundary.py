"""Static dependency boundaries for the reusable Runtime Kernel package."""

from __future__ import annotations

import ast
from pathlib import Path


RUNTIME_ROOT = Path(__file__).parents[2] / "src" / "agent_runtime"
FORBIDDEN_ROOTS = {"app", "db"}


def test_agent_runtime_does_not_import_agenthub_application_or_database() -> None:
    violations: list[str] = []

    for path in sorted(RUNTIME_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                modules = [node.module]

            for module in modules:
                if module.split(".", 1)[0] in FORBIDDEN_ROOTS:
                    relative_path = path.relative_to(RUNTIME_ROOT.parent)
                    violations.append(f"{relative_path}:{node.lineno} imports {module}")

    assert violations == [], "Runtime dependency boundary violations:\n" + "\n".join(violations)
