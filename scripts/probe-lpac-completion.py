"""Finite P1b checklist runner. A missing prerequisite never counts as a pass."""

import argparse
import ctypes
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

from lpac_probe.outer_job import run


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--namespace-experiment")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    if (sys.platform != "win32" or ctypes.windll.shell32.IsUserAnAdmin()
            or output.parent != repo / "var" or not output.name.startswith("l4a-completion-")
            or output.exists()):
        parser.error("Ordinary user; fresh repository var/l4a-completion-* output required")
    output.mkdir()
    report = {"gate": "not_passed", "checks": {}, "reports": {}, "not_executed": [],
              "namespace_experiment": args.namespace_experiment,
              "source_sha256": {str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in (Path(__file__), repo / "scripts/lpac_probe/outer_job.py",
                                          repo / "scripts/lpac_probe/gate_validation.py",
                                          repo / "scripts/lpac_probe/symbolic_fixture.py")},
              "source_commit": subprocess.check_output(["git", "rev-parse", "HEAD"],
                                                       cwd=repo, text=True).strip()}
    try:
        for name, script, switches in (
            ("policies", "probe-lpac-quiescent.py", ["--remaining-gate"]),
            ("isolation", "probe-lpac-isolation.py", ["--nested-jobs"]),
            ("objects", "probe-lpac-standing.py", []),
        ):
            prefix = {"policies": "quiescent", "isolation": "isolation", "objects": "standing"}[name]
            target = repo / "var" / ("l4a-" + prefix + "-completion-" + uuid4().hex)
            if name == "policies" and args.namespace_experiment:
                switches += ["--toolchain", "--namespace-experiment", args.namespace_experiment]
            if name == "isolation" and args.namespace_experiment:
                switches += ["--symbolic-fixture-id", args.namespace_experiment]
            child = run([sys.executable, str(repo / "scripts" / script), "--output", str(target),
                         *switches], repo, log_path=output / (name + ".log"))
            report["reports"][name] = {"path": str(target / "report.json"), "outer_job": child}
            path = target / "report.json"
            data = json.loads(path.read_text())
            report["reports"][name] = {"path": str(path), "sha256": hashlib.sha256(
                path.read_bytes()).hexdigest(), "outer_job": child}
            report["checks"][name] = child["exit_code"] == 0 and data.get(
                "subset_passed" if name != "isolation" else "passed") is True
            if name == "policies":
                report["checks"]["toolchain"] = data.get("toolchain_passed") is True
                if not args.namespace_experiment:
                    report["not_executed"].append("fixed_namespace_toolchain")
            if name == "isolation":
                report["checks"]["desktop_job_inherited"] = data["host_in_outer_job"]
                report["checks"]["run_jobs_closed"] = data["parent_jobs_closed"]
                report["coverage_limits"] = data.get("not_executed", {})
                report["checks"]["symbolic_link"] = (
                    not report["coverage_limits"] and data.get("symbolic_link_cleaned") is True)
            if not report["checks"][name]:
                raise RuntimeError("Required native gate failed: " + name)
        required = {"policies", "isolation", "objects", "toolchain", "desktop_job_inherited",
                    "run_jobs_closed", "symbolic_link"}
        if set(report["checks"]) == required and all(report["checks"].values()):
            report["gate"] = "pending_initialization_cleanup"
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if report["gate"] == "pending_initialization_cleanup" else 1


if __name__ == "__main__":
    raise SystemExit(main())
