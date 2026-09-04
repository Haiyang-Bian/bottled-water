"""Finish the source-root migration and provider metadata ownership."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
path = ROOT / "src/model_provider/factory.py"
text = path.read_text(encoding="utf-8")
start = text.index("# 内置 Provider 元数据")
end = text.index("\n\ndef create_provider", start)
metadata = text[start:end]
tail = text[text.index("def get_builtin_providers"):]
dest = ROOT / "backend/src/app/services/provider_catalog.py"
dest.write_text('"""Web provider form metadata; not part of the execution system."""\n\n' + metadata + "\n\n" + tail, encoding="utf-8")
text = text[:start] + text[end:text.index("def get_builtin_providers")]
text = text.replace("from .providers.ark import ArkProvider\nfrom .providers.deepseek import DeepSeekProvider\nfrom .providers.openai_compatible import OpenAICompatibleProvider\n", "from importlib import import_module\n")
text = text.replace('Dict[str, type]', 'Dict[str, type | tuple[str, str]]')
for name, module in (("ArkProvider", "ark"), ("DeepSeekProvider", "deepseek"), ("OpenAICompatibleProvider", "openai_compatible")):
    text = text.replace(f": {name},", f': ("{module}", "{name}"),')
text = text.replace('    model = config.get("model", "unknown")', '    if isinstance(provider_cls, tuple):\n        module, name = provider_cls\n        provider_cls = getattr(import_module(f"model_provider.providers.{module}"), name)\n\n    model = config.get("model", "unknown")')
text = text.replace('name: cls.__doc__ or name', 'name: (cls[1] if isinstance(cls, tuple) else cls.__doc__) or name')
path.write_text(text, encoding="utf-8")
path = ROOT / "src/model_provider/__init__.py"
path.write_text(path.read_text(encoding="utf-8").replace('    get_builtin_providers,\n', '').replace('    "get_builtin_providers",\n', ''), encoding="utf-8")
# Metadata has one owner. Rewrite imports, preserving mixed import statements.
import ast
for directory in (ROOT / "backend/src", ROOT / "backend/tests"):
    for path in directory.rglob("*.py"):
        text = path.read_text(encoding="utf-8-sig")
        lines = text.splitlines(keepends=True)
        changes = []
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.module in {"model_provider", "model_provider.factory"} and any(a.name == "get_builtin_providers" for a in node.names):
                indent = " " * node.col_offset
                remaining = [a.name + (f" as {a.asname}" if a.asname else "") for a in node.names if a.name != "get_builtin_providers"]
                replacement = f"{indent}from app.services.provider_catalog import get_builtin_providers\n"
                if remaining:
                    replacement += f"{indent}from {node.module} import {', '.join(remaining)}\n"
                changes.append((node.lineno-1, node.end_lineno, replacement))
        for start, end, replacement in sorted(changes, reverse=True):
            lines[start:end] = [replacement]
        if changes:
            path.write_text("".join(lines), encoding="utf-8")
