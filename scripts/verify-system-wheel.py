"""Check that the selected wheel contains this version and these exact shared sources."""

import hashlib
import json
import tomllib
import zipfile
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    version = tomllib.loads((root / "pyproject.toml").read_text())['project']['version']
    wheel = root / "dist" / f"agenthub_system-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        for source in (root / "src").rglob("*.py"):
            relative = source.relative_to(root / "src").as_posix()
            if archive.read(relative) != source.read_bytes():
                raise ValueError(f"Wheel/source mismatch: {relative}")
    evidence = {"version": version, "wheel": str(wheel),
                "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest()}
    (wheel.parent / f"agenthub_system-{version}.json").write_text(
        json.dumps(evidence, indent=2), encoding="utf-8")
    print(wheel)


if __name__ == "__main__":
    main()
