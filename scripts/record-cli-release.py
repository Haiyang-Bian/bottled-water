"""Bind final CLI acceptance observations to the tagged wheel and installed files."""

import hashlib
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    version = "0.1.7"
    tag = f"agenthub-v{version}"
    wheel = ROOT / "dist" / f"agenthub_system-{version}-py3-none-any.whl"
    installed = ROOT / "var/cli-install-validation/tools/agenthub-system"
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.endswith(".py"):
                assert (installed / "Lib/site-packages" / name).read_bytes() == archive.read(name), name
    evidence = {
        "version": version, "tag": tag,
        "source_commit": subprocess.check_output(["git", "rev-parse", tag], cwd=ROOT).decode().strip(),
        "wheel": wheel.name, "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "installed_sources_match_wheel": True,
        "tests": {}, "real_provider": {},
    }
    for name in ("cli-017-unit-final.xml", "shared-017.xml", "web-017.xml", "upgrade-0.1.7.xml"):
        tree = ET.parse(ROOT / "var" / name)
        suites = list(tree.getroot().iter("testsuite"))
        evidence["tests"][name] = {
            field: sum(int(s.get(field, 0)) for s in suites)
            for field in ("tests", "failures", "errors", "skipped")
        }
        assert not evidence["tests"][name]["failures"] and not evidence["tests"][name]["errors"]
    for key, directory in (("conpty", "terminal-017-release"),
                           ("deepseek_recovery", "live-ui-017-release"),
                           ("deepseek_plain", "live-plain-017-release")):
        value = json.loads((ROOT / "var" / directory / "acceptance.json").read_text(encoding="utf-8"))
        assert value["status"] == "passed", directory
        evidence[key] = value
    evidence["real_provider"]["openai_compatible"] = "not_run: no explicit profile configured"
    evidence["install_log"] = "var/install-017-final.log"
    evidence["desktop_build_log"] = "var/sidecar-017-release-build.log"
    evidence["desktop_smoke_log"] = "var/sidecar-017-release-smoke.log"
    assert "passed" in (ROOT / evidence["desktop_smoke_log"]).read_text(encoding="utf-8")
    target = ROOT / "dist/agenthub_system-0.1.7-acceptance.json"
    target.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(target)


if __name__ == "__main__":
    main()
