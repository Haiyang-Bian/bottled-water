"""Read-only ACL differences for the owned standing-policy fixture."""
import argparse
import json
from pathlib import Path

from lpac_probe.standing import fixture_acl

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True)
args = parser.parse_args()
root = Path(args.output).resolve()
repo = Path(__file__).resolve().parents[1]
if not root.is_relative_to(repo / "var"):
    parser.error("Only repository-local probe fixtures")
report = json.loads((root / "report.json").read_text())
diff = {}
for path, previous in report["original_acl"].items():
    item = (root / path).resolve()
    if not item.is_relative_to(root):
        raise RuntimeError("Invalid fixture report path")
    current = fixture_acl(item) if item.exists() else None
    if previous != current:
        diff[path] = {"previous": previous, "current": current}
print(json.dumps(diff, indent=2))
