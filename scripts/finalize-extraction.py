"""Finish explicit import and streaming ownership updates for the migration."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src/agent_runtime/strategies/policies.py"
text = path.read_text(encoding="utf-8")
start = text.index("class SingleAgentPolicy:")
end = text.index("class WorkflowPolicy:", start)
path.write_text(text[:start] + text[end:], encoding="utf-8")
for directory in (ROOT / "src", ROOT / "backend/src", ROOT / "backend/tests"):
    for path in directory.rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        lines = text.splitlines(keepends=True)
        changes = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module == "agent_runtime.strategies.policies" and any(a.name == "SingleAgentPolicy" for a in node.names):
                indent = " " * node.col_offset
                remaining = [a.name for a in node.names if a.name != "SingleAgentPolicy"]
                replacement = f"{indent}from agent_subsystems.scheduling.single_agent import SingleAgentPolicy\n"
                if remaining:
                    replacement += f"{indent}from agent_runtime.strategies.policies import {', '.join(remaining)}\n"
                changes.append((node.lineno-1, node.end_lineno, replacement))
        for start, end, replacement in sorted(changes, reverse=True):
            lines[start:end] = [replacement]
        if changes:
            path.write_text("".join(lines), encoding="utf-8")

for relative in ("src/model_provider/providers/openai_compatible.py",):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    start = text.index("        async for response_chunk in stream:")
    end = text.index("\n    async def list_models", start)
    block = text[start:end]
    block = block.replace("        async for response_chunk in stream:\n", "        async for response_chunk in stream:\n            if response_chunk.usage:\n                yield StreamChunk(usage=response_chunk.usage.model_dump())\n")
    wrapped = "        try:\n" + "\n".join("    " + l for l in block.rstrip().splitlines())
    wrapped += "\n        finally:\n            close = getattr(stream, 'close', None)\n            if close is not None:\n                await close()\n"
    text = text[:start] + wrapped + text[end:]
    text = text.replace("    async def list_models", "    async def aclose(self):\n        await self.client.close()\n\n    async def list_models", 1)
    path.write_text(text, encoding="utf-8")

for relative in ("src/agent_subsystems/execution/agent_loop.py", "src/model_provider/core/streaming.py"):
    path = ROOT / relative
    text = path.read_text(encoding="utf-8")
    # Consume the final usage-only chunk after finish_reason, and always close streams.
    if relative.endswith("agent_loop.py"):
        text = text.replace("        async for chunk in stream:\n", "        usage = None\n        finish_reason = None\n        async for chunk in stream:\n            if getattr(chunk, 'usage', None) is not None:\n                usage = chunk.usage\n            if chunk.finish_reason:\n                finish_reason = chunk.finish_reason\n", 1)
        text = text.replace("            if chunk.finish_reason:\n                break", "            # Continue to consume the usage-only trailer.")
        text = text.replace('            reasoning_content="".join(reasoning_parts),\n', '            reasoning_content="".join(reasoning_parts),\n            usage=usage,\n            finish_reason=finish_reason,\n')
        start = text.index("        async for chunk in stream:")
        end = text.index("        # 组装最终响应", start)
        block = text[start:end]
        text = text[:start] + "        try:\n" + "\n".join("    " + l for l in block.rstrip().splitlines()) + "\n        finally:\n            await stream.aclose()\n\n" + text[end:]
    else:
        text = text.replace("    budget_exhausted = False", "    usage = None\n    finish_reason = None\n    budget_exhausted = False")
        text = text.replace("        async for chunk in stream:\n", "        async for chunk in stream:\n            if getattr(chunk, 'usage', None) is not None:\n                usage = chunk.usage\n            if chunk.finish_reason:\n                finish_reason = chunk.finish_reason\n")
        text = text.replace('        reasoning_content="".join(reasoning_parts),', '        reasoning_content="".join(reasoning_parts),\n        usage=usage,\n        finish_reason=finish_reason,')
    ast.parse(text)
    path.write_text(text, encoding="utf-8")

(ROOT / "backend/uv.lock").unlink()
