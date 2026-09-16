"""Content and object manifest for a host-selected, immutable tool copy.

This check never selects software from a model path. It detects changes at Run
boundaries; it does not claim to monitor or lock a live filesystem tree.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import stat

from agent_contracts.errors import OperationError


def inventory(root: Path) -> dict:
    entries = {}

    def visit(path):
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or getattr(before, "st_file_attributes", 0) & 0x400:
            raise OperationError("dependency_alias", "Tool copies cannot contain reparse points")
        directory = stat.S_ISDIR(before.st_mode)
        if not directory and (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1):
            raise OperationError("dependency_alias", "Tool copies require ordinary single-link files")
        value = {"identity": [before.st_dev, before.st_ino], "directory": directory}
        if directory:
            for child in sorted(path.iterdir()):
                visit(child)
        else:
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while block := stream.read(256 * 1024):
                    digest.update(block)
            value.update(size=before.st_size, sha256=digest.hexdigest())
        after = path.lstat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise OperationError("dependency_changed", "Dependency changed during inspection")
        entries[str(path.relative_to(root))] = value

    visit(root)
    if not entries["."]["directory"]:
        raise OperationError("dependency_invalid", "A tool-copy directory is required")
    return entries


@dataclass(frozen=True)
class DependencyManifest:
    root: Path
    encoded: str
    digest: str

    @classmethod
    def capture(cls, root: Path):
        root = Path(root).absolute()
        # Inspect before resolving so a root alias is never silently accepted.
        for parent in (root, *root.parents):
            value = parent.lstat()
            if stat.S_ISLNK(value.st_mode) or getattr(value, "st_file_attributes", 0) & 0x400:
                raise OperationError("dependency_alias", "Dependency ancestor is an alias")
        encoded = json.dumps(inventory(root), sort_keys=True, separators=(",", ":"))
        return cls(root, encoded, hashlib.sha256(encoded.encode()).hexdigest())

    def verify(self) -> None:
        try:
            # An ancestor can become a junction while all descendant object IDs
            # remain unchanged. Recheck the complete path at every Run boundary.
            actual = self.capture(self.root).encoded
        except (OSError, OperationError) as exc:
            raise OperationError("dependency_changed", "Tool copy cannot be verified") from exc
        if actual != self.encoded:
            raise OperationError("dependency_changed", "Tool copy changed; prepare a new instance")
