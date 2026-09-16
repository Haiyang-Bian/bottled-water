"""Fixed initialization dependencies and preflight; never installs or elevates."""

import os

import pytest

from agent_adapters.local.windows_namespace import TARGETS, open_target, read_acl


@pytest.mark.skipif(os.name != "nt", reason="Native system query, no permission changes")
@pytest.mark.parametrize("target", TARGETS, ids=lambda value: value.path)
def test_ordinary_host_can_verify_fixed_initialization(target):
    import ctypes
    assert not ctypes.windll.shell32.IsUserAnAdmin(), "Run this test without elevation"
    handle = open_target(target, write=False)
    try:
        assert read_acl(handle).GetAceCount() > 0
    finally:
        handle.Close()


def test_initializer_rejects_unprotected_code_path():
    if os.name != "nt":
        pytest.skip("Windows component ACL")
    from pathlib import Path
    from agent_adapters.local.sandbox_admin import check_protected
    with pytest.raises(RuntimeError, match="protected installation"):
        check_protected(Path(__file__).parent)


@pytest.mark.skipif(os.environ.get("AGENTHUB_RUN_SANDBOX_STAGE") != "1",
                   reason="Explicit isolated interpreter copy preflight required")
def test_isolated_component_preflight_before_elevation(tmp_path):
    from uuid import uuid4
    from agent_adapters.local.sandbox_install import stage_component
    hashes = stage_component(tmp_path / "component", uuid4().hex)
    assert "python\\pywintypes.py" in hashes
    assert "python\\pywintypes311.dll" in hashes
    assert not (tmp_path / "component" / "initialization.json").exists()
