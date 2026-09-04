"""Product-policy tests explicitly install the Web execution extension."""
import ast
from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "backend/tests/test_agent_runtime/test_agent_loop.py"
text = path.read_text(encoding="utf-8")
lines = text.splitlines(keepends=True)
offsets, offset = [], 0
for line in lines:
    offsets.append(offset)
    offset += len(line)
changes = []
for node in ast.walk(ast.parse(text)):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "AgentLoop":
        end = offsets[node.end_lineno-1] + node.end_col_offset - 1
        changes.append((end, ", extension_factory=WebExecutionExtension"))
for end, addition in sorted(changes, reverse=True):
    text = text[:end] + addition + text[end:]
text = text.replace("from model_provider import ChatResponse, StreamChunk", "from model_provider import ChatResponse, StreamChunk\nfrom app.services.execution_extension import WebExecutionExtension")
for name in ("_build_prompt", "_format_blackboard"):
    text = text.replace(f"loop.{name}", f"loop.extension.{name}")
path.write_text(text, encoding="utf-8")
