"""Synchronize Python distribution releases without touching client versions."""

import argparse
import re
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    args = parser.parse_args()
    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("Expected a major.minor.patch version")
    root = Path(__file__).resolve().parents[1]
    for relative in ("pyproject.toml", "backend/pyproject.toml"):
        path = root / relative
        source = path.read_text(encoding="utf-8")
        source = re.sub(r'^version = "[^"]+"', f'version = "{args.version}"',
                        source, count=1, flags=re.MULTILINE)
        source = re.sub(r'agenthub-system==[\d.]+', f'agenthub-system=={args.version}', source)
        path.write_text(source, encoding="utf-8")
    for relative in ("src/agent_cli/main.py", "backend/src/app/main.py"):
        path = root / relative
        source = path.read_text(encoding="utf-8")
        source = re.sub(r'version="agenthub [\d.]+"', f'version="agenthub {args.version}"', source)
        source = re.sub(r'version="\d+\.\d+\.\d+"', f'version="{args.version}"', source)
        path.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
