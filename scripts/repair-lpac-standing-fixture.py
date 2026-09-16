"""Repair a just-created, fully drained probe fixture; never a user-directory repair tool."""
import argparse
import json
from pathlib import Path

from agent_adapters.local.windows_lpac import LpacProfile
from lpac_probe.standing import cleanup_tree, fixture_acl, restore_fixture_inheritance, tree

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
root = Path(args.output).resolve()
repo = Path(__file__).resolve().parents[1]
if root.parent != repo / "var" or not root.name.startswith("l4a-standing-"):
    parser.error("Only this probe's repository-local fixtures")
report = json.loads((root / "report.json").read_text())
if not report.get("results") or not all(r.get("job_drained") for r in report["results"]):
    parser.error("Need affirmative Job-drained evidence; no age-based recovery")
repair_file = root / "repair.json"
if repair_file.exists():
    parser.error("A repair record already exists")
# Validate the entire owned tree and report before mutating anything.
list(tree(root))
for change in report["inheritance_changes"]:
    if not (root / change["path"]).resolve().is_relative_to(root / "Tree"):
        parser.error("Invalid recorded boundary")
profiles = [LpacProfile(record["name"]) for record in report["profiles"]]
intent = {"state": "intent", "profiles": [p.name for p in profiles]}
repair_file.write_text(json.dumps(intent), encoding="utf-8")
cleanup_tree(root, set(report["policies"]) | {p["sid"] for p in report["profiles"]})
for change in report["inheritance_changes"]:
    restore_fixture_inheritance(root / change["path"], report["original_acl"][change["path"]])
for profile in profiles:
    profile.delete()
intent["profiles_deleted"] = True
intent["unrelated_acl_preserved"] = all(
    (root / path).exists() and fixture_acl(root / path) == previous
    for path, previous in report["original_acl"].items() if not path.startswith("Tree\\Work\\")
)
intent["state"] = "verified" if intent["unrelated_acl_preserved"] else "incomplete"
repair_file.write_text(json.dumps(intent, indent=2), encoding="utf-8")
print(json.dumps({"record": str(repair_file), "state": intent["state"]}))
raise SystemExit(0 if intent["state"] == "verified" else 1)
