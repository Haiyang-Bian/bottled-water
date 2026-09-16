"""Read-only source/test inventory; writes one explicitly named new evidence file."""

import argparse
import ast
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import tomllib
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, action="append", default=[])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if not output.is_relative_to(root / "var"):
        raise ValueError("Evidence must be a new file in this repository's var directory")
    paths = [path for folder in (root / "backend/src", root / "backend/alembic", root / "src")
             for path in folder.rglob("*.py")]
    paths += list((root / "backend").glob("*.py"))
    paths += [root / name for name in ("desktop-client/scripts/build-sidecar.ps1", "pyproject.toml",
                                       "uv.lock", "backend/pyproject.toml")]
    hashes = {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(paths)}
    reports = []
    for path in args.report:
        xml = ET.parse(path).getroot()
        suites = [xml] if xml.tag == "testsuite" else xml.findall("testsuite")
        reports.append({"path": str(path.resolve().relative_to(root)),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "suites": [suite.attrib for suite in suites]})
    with (root / "pyproject.toml").open("rb") as stream:
        version = tomllib.load(stream)["project"]["version"]
    migration = ast.parse((root / "src/agent_adapters/storage/migration.py").read_text(encoding="utf-8"))
    schema = next(ast.literal_eval(node.value) for node in migration.body
                  if isinstance(node, ast.Assign)
                  and any(isinstance(target, ast.Name) and target.id == "SCHEMA_VERSION"
                          for target in node.targets))
    result = {"source_commit": subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "working_tree": subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=no"], cwd=root, text=True).strip(),
        "os": platform.platform(), "sidecar_input_count": len(hashes), "sidecar_inputs": hashes,
        "sidecar_build_executed": False, "reports": reports,
        "formal_cli_native": "not_evaluated_by_this_recorder",
        "DeepSeek": "not_evaluated_by_this_recorder", "OpenAI_compatible_live": "not_evaluated",
        "release_version": version, "development_schema": schema}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, ensure_ascii=True, indent=2)
    print(json.dumps({"evidence": str(output), "source_files": len(hashes),
                      "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
