"""One-time, bounded source-root migration for the approved CLI extraction."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def inside(path: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise RuntimeError(f"Outside repository: {resolved}")
    return resolved


for package in ("agent_runtime", "model_provider"):
    source = inside(ROOT / "backend/src" / package)
    target = inside(ROOT / "src" / package)
    if source.exists():
        if target.exists():
            raise RuntimeError(f"Destination exists: {target}")
        source.rename(target)

# Source modules that leave the Kernel export surface. Callers import their owner.
EXPORTS = {
    "AgentLoopExecutor": "agent_runtime.runtime.agent_executor",
    "SingleAgentPolicy": "agent_runtime.strategies.policies",
    "TeamLeadPolicy": "agent_runtime.strategies.policies",
    "WorkflowPolicy": "agent_runtime.strategies.policies",
    "CollaborativeTeamPolicy": "agent_runtime.strategies.collaborative",
    "ToolRegistry": "agent_runtime.tools.registry",
    "ToolExecutorImpl": "agent_runtime.tools.executor",
}
for tree in (ROOT / "src", ROOT / "backend/src", ROOT / "backend/tests"):
    for path in tree.rglob("*.py"):
        contents = path.read_text(encoding="utf-8-sig")
        lines = contents.splitlines(keepends=True)
        replacements = []
        for node in ast.walk(ast.parse(contents)):
            if isinstance(node, ast.ImportFrom) and node.module == "agent_runtime":
                groups = {}
                for alias in node.names:
                    groups.setdefault(EXPORTS.get(alias.name, "agent_runtime"), []).append(
                        alias.name + (f" as {alias.asname}" if alias.asname else "")
                    )
                if len(groups) > 1 or next(iter(groups)) != "agent_runtime":
                    indent = " " * node.col_offset
                    replacements.append((node.lineno - 1, node.end_lineno, "".join(
                        f"{indent}from {module} import {', '.join(names)}\n"
                        for module, names in groups.items()
                    )))
        for start, end, replacement in sorted(replacements, reverse=True):
            lines[start:end] = [replacement]
        updated = "".join(lines)
        if tree == ROOT / "src":
            updated = updated.replace("from common.logger import", "from agent_contracts.logging import")
        updated = updated.replace("from model_provider.core.streaming import OutputTokenLimitExceeded,", "from agent_contracts.errors import OutputTokenLimitExceeded\nfrom model_provider.core.streaming import")
        updated = updated.replace("from model_provider.core.streaming import OutputTokenLimitExceeded\n", "from agent_contracts.errors import OutputTokenLimitExceeded\n")
        if updated != contents:
            path.write_text(updated, encoding="utf-8")

path = ROOT / "src/agent_runtime/__init__.py"
text = path.read_text(encoding="utf-8")
text = "\n".join(line for line in text.splitlines() if not any(
    (line.startswith("from ") and any(name in line for name in EXPORTS))
    or line.strip() == f'"{name}",' for name in EXPORTS
)) + "\n"
path.write_text(text, encoding="utf-8")

path = ROOT / "src/model_provider/core/streaming.py"
text = path.read_text(encoding="utf-8")
text = text.replace('class OutputTokenLimitExceeded(RuntimeError):\n    """Raised after actively closing a stream that exhausted its output budget."""', 'from agent_contracts.errors import OutputTokenLimitExceeded')
path.write_text(text, encoding="utf-8")

# Fix source-path lookups in tests and launchers; do not rewrite application data.
for path in (ROOT / "backend").glob("*.py"):
    text = path.read_text(encoding="utf-8-sig")
    if "sys.path" in text:
        text = "from pathlib import Path as _SystemPath\nimport sys as _system_sys\n_system_sys.path.insert(0, str(_SystemPath(__file__).resolve().parents[1] / 'src'))\n" + text if not text.startswith("from __future__") else text
        # Preserve module docstrings/future imports: launchers receive paths through installation.

