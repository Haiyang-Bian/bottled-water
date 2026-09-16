"""Fixed native attempts for two different simultaneous policy capabilities."""

import argparse
import json
import time
from pathlib import Path


def attempt(action):
    try:
        return {"allowed": True, "value": action()}
    except OSError as exc:
        return {"allowed": False, "errno": exc.errno, "winerror": exc.winerror}


def main():
    parser = argparse.ArgumentParser()
    for name in ("root", "own", "other"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--label", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    result = {}
    for label in ("Work", "Archive", "Private"):
        result["read_" + label] = attempt(
            lambda label=label: (args.root / label / "sample.txt").read_text())
        result["write_" + label] = attempt(
            lambda label=label: (args.root / label / (args.label + ".txt")).write_text("created"))
    result["own"] = attempt(lambda: (args.own / "private.txt").read_text())
    result["other"] = attempt(lambda: (args.other / "private.txt").read_text())
    result["read_special"] = attempt(lambda: (args.root / "Work/Special/sample.txt").read_text())
    result["write_special"] = attempt(
        lambda: (args.root / "Work/Special" / (args.label + ".txt")).write_text("created"))
    time.sleep(0.75)
    result["window"] = [started, time.monotonic()]
    print(json.dumps(result))


if __name__ == "__main__":
    main()
