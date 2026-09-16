"""One fixed repository-owned symbolic-link fixture; no arbitrary target arguments."""

import os
from pathlib import Path

from .namespace import capability_name


def paths(repo, experiment):
    capability_name(experiment)
    root = Path(repo) / "var" / ("l4a-symbolic-" + experiment)
    return root, root / "link-to-private", root / "Private"


def create(repo, experiment):
    root, link, target = paths(repo, experiment)
    for item in (target, root, *root.parents):
        if item.lstat().st_file_attributes & 0x400:
            raise RuntimeError("Refuse an alias in symbolic-link fixture ancestors")
    if os.path.lexists(link) or set(p.name for p in root.iterdir()) != {"Private"}:
        raise RuntimeError("Symbolic-link fixture is not fresh")
    if set(p.name for p in target.iterdir()) != {"sample.txt"}:
        raise RuntimeError("Unexpected symbolic-link target contents")
    if (target / "sample.txt").lstat().st_file_attributes & 0x400:
        raise RuntimeError("Fixture sample is an alias")
    os.symlink(target, link, target_is_directory=True)
    return {"path": str(link), "target": str(target), "created": True}


def cleanup(repo, experiment):
    _, link, target = paths(repo, experiment)
    if os.path.lexists(link):
        if not link.is_symlink() or Path(os.readlink(link)) != target:
            raise RuntimeError("Symbolic fixture changed; refuse guessed cleanup")
        link.rmdir()  # Remove this exact link, never traverse the target.
        return "removed"
    return "transferred_to_probe_or_removed"
