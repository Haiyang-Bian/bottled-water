"""Fixed file attempts inside disposable LPAC movement fixtures; no host callbacks."""

import argparse
import json
from pathlib import Path


def attempt(action):
    try:
        return {"allowed": True, "value": action()}
    except OSError as exc:
        return {"allowed": False, "errno": exc.errno, "winerror": exc.winerror}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--moved", action="store_true")
    args = parser.parse_args()
    paths = {
        "work": args.root / "Work/sample.txt",
        "archive": args.root / "Archive/sample.txt",
        "private": args.root / "Private/sample.txt",
    }
    if args.moved:
        paths.update({
            "moved_archive": args.root / "Archive/from-work.txt",
            "moved_private": args.root / "Private/from-work.txt",
            "copied_archive": args.root / "Archive/copy.txt",
            "copied_private": args.root / "Private/copy.txt",
            "detached_root": args.root / "DetachedWork/sample.txt",
            "moved_directory": args.root / "Private/folder/nested.txt",
        })
    result = {}
    for name, path in paths.items():
        result["read_" + name] = attempt(lambda p=path: p.read_text(encoding="utf-8"))
        result["write_" + name] = attempt(lambda p=path: p.write_text("changed", encoding="utf-8"))
    print(json.dumps(result))


if __name__ == "__main__":
    main()
