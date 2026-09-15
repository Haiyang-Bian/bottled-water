"""Bind each local release wheel to its immutable Git tag and source bytes."""

import argparse
import hashlib
import io
import json
import subprocess
import tarfile
import zipfile
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args], cwd=ROOT)


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--through", type=int, choices=range(1, 8), default=7)
    args = parser.parse_args()
    records = []
    for minor in range(1, args.through + 1):
        version = f"0.1.{minor}"
        tag = f"agenthub-v{version}"
        commit = git("rev-parse", f"{tag}^{{commit}}").decode().strip()
        wheel = ROOT / "dist" / f"agenthub_system-{version}-py3-none-any.whl"
        sources = tarfile.open(fileobj=io.BytesIO(git("archive", tag, "src")))
        with sources, zipfile.ZipFile(wheel) as archive:
            for member in sources.getmembers():
                path = member.name
                if member.isfile() and path.endswith(".py"):
                    expected = sources.extractfile(member).read()
                    actual = archive.read(path.removeprefix("src/"))
                    # Git canonicalizes text to LF; a Windows wheel may retain CRLF.
                    if actual.replace(b"\r\n", b"\n") != expected.replace(b"\r\n", b"\n"):
                        raise ValueError(f"Source mismatch: {tag} {path}")
        record = {
            "version": version,
            "tag": tag,
            "source_commit": commit,
            "wheel": wheel.name,
            "sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
            "source_verified": True,
        }
        (wheel.parent / f"agenthub_system-{version}.json").write_text(
            json.dumps(record, indent=2), encoding="utf-8"
        )
        records.append(record)
    (ROOT / "dist" / "harness-releases.json").write_text(
        json.dumps(records, indent=2), encoding="utf-8"
    )
    print(json.dumps(records, indent=2))


if __name__ == "__main__":
    main()
