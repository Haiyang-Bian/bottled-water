"""Bind explicit acceptance observations to the metadata-selected installed wheel."""

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-ref", default="HEAD")
    parser.add_argument("--installed", type=Path,
                        default=ROOT / "var/cli-install-validation/tools/agenthub-system")
    parser.add_argument("--test", action="append", type=Path, required=True)
    parser.add_argument("--observation", action="append", default=[], help="name=JSON path")
    parser.add_argument("--not-run", action="append", default=[])
    parser.add_argument("--desktop-build", type=Path, required=True)
    parser.add_argument("--desktop-smoke", type=Path, required=True)
    args = parser.parse_args()
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())['project']['version']
    wheel = ROOT / "dist" / f"agenthub_system-{version}-py3-none-any.whl"
    commit = subprocess.check_output(["git", "rev-parse", args.source_ref], cwd=ROOT).decode().strip()
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith(".py"):
                contents = archive.read(name)
                assert (args.installed / "Lib/site-packages" / name).read_bytes() == contents, name
                assert (ROOT / "src" / name).read_bytes() == contents, name
                committed = subprocess.check_output(["git", "show", f"{commit}:src/{name}"], cwd=ROOT)
                assert committed.replace(b"\r\n", b"\n") == contents.replace(b"\r\n", b"\n"), name
        metadata = archive.read(f"agenthub_system-{version}.dist-info/METADATA").decode()
        assert f"Version: {version}\n" in metadata
    evidence = {
        "version": version, "source_commit": commit,
        "wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "installed_sources_match_wheel": True,
        "source_commit_matches_wheel": True,
        "tests": {}, "not_run": args.not_run,
    }
    for path in args.test:
        suites = list(ET.parse(path).getroot().iter("testsuite"))
        counts = {
            field: sum(int(s.get(field, 0)) for s in suites)
            for field in ("tests", "failures", "errors", "skipped")
        }
        assert counts["tests"] and not counts["failures"] and not counts["errors"], path
        evidence["tests"][str(path)] = counts
    for item in args.observation:
        key, path = item.split("=", 1)
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        assert value["status"] == "passed", path
        evidence[key] = value
    evidence["desktop_build_log"] = str(args.desktop_build)
    evidence["desktop_smoke_log"] = str(args.desktop_smoke)
    assert args.desktop_build.is_file()
    assert "passed" in args.desktop_smoke.read_text(encoding="utf-8")
    target = wheel.with_name(f"agenthub_system-{version}-acceptance.json")
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    wheel.with_suffix(".whl.sha256").write_text(
        f"{evidence['wheel_sha256']}  {wheel.name}\n", encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
