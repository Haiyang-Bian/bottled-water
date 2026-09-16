"""Record finite native gate results only after fixed initialization is removed."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from lpac_probe.gate_validation import assess
from lpac_probe.namespace import capability_name


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", required=True)
    args = parser.parse_args()
    capability_name(args.experiment)
    root = Path(__file__).resolve().parents[1] / "var"
    path = root / ("l4a-completion-" + args.experiment) / "report.json"
    initialization_path = root / ("l4a-namespace-" + args.experiment + ".json")
    report = json.loads(path.read_text())
    initialization = json.loads(initialization_path.read_text())
    report.update(assess(report, initialization))
    report["initialization"] = {"path": str(initialization_path), "sha256": hashlib.sha256(
        initialization_path.read_bytes()).hexdigest(), "status": initialization["status"]}
    pending = path.with_suffix(".pending")
    pending.write_text(json.dumps(report, indent=2), encoding="utf-8")
    os.replace(pending, path)
    print(json.dumps({"report": str(path), "gate": report["gate"],
                      "errors": report["gate_errors"]}))
    return 0 if report["gate"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
