"""Run-boundary changes must invalidate the selected tool copy."""

import os
import sys

import pytest

from agent_adapters.local.dependencies import DependencyManifest
from agent_contracts.errors import OperationError


@pytest.mark.parametrize("change", ["modify", "missing", "extra", "replace", "directory", "hardlink"])
def test_dependency_changes_are_rejected(tmp_path, change):
    root = tmp_path / "copy"
    root.mkdir()
    file = root / "tool.exe"
    file.write_bytes(b"original")
    manifest = DependencyManifest.capture(root)
    manifest.verify()
    if change == "modify":
        file.write_bytes(b"modified")
    elif change == "missing":
        file.unlink()
    elif change == "extra":
        (root / "injected.dll").write_bytes(b"unexpected")
    elif change == "replace":
        replacement = root / "replacement"
        replacement.write_bytes(file.read_bytes())
        replacement.replace(file)
    elif change == "directory":
        (root / "plugins").mkdir()
    else:
        os.link(file, root / "alias")
    with pytest.raises(OperationError) as error:
        manifest.verify()
    assert error.value.code == "dependency_changed"


def test_manifest_detects_root_replacement(tmp_path):
    root = tmp_path / "copy"
    root.mkdir()
    (root / "tool.exe").write_bytes(b"same")
    manifest = DependencyManifest.capture(root)
    root.rename(tmp_path / "old")
    root.mkdir()
    (root / "tool.exe").write_bytes(b"same")
    with pytest.raises(OperationError):
        manifest.verify()


def test_manifest_rejects_replaced_ancestor_even_with_same_objects(tmp_path):
    parent = tmp_path / "parent"
    root = parent / "copy"
    root.mkdir(parents=True)
    (root / "tool.exe").write_bytes(b"same")
    manifest = DependencyManifest.capture(root)
    moved = tmp_path / "moved"
    parent.rename(moved)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(moved), str(parent))
    else:
        parent.symlink_to(moved, target_is_directory=True)
    try:
        with pytest.raises(OperationError) as error:
            manifest.verify()
        assert error.value.code == "dependency_changed"
    finally:
        # Only unlink the exact alias created above; keep its target untouched.
        if sys.platform == "win32":
            parent.rmdir()
        else:
            parent.unlink()
    assert (moved / "copy/tool.exe").read_bytes() == b"same"
