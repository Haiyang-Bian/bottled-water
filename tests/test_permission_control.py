"""Local pipe protocol, identity rejection and event-driven shutdown; no ACL writes."""

import os

import pytest

from agent_adapters.local.windows_permission_ipc import (
    PermissionControlServer, process_identity, request_control,
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows pipe")


def test_control_roundtrip_and_wrong_environment_rejection():
    seen = []
    def handler(request, peer):
        seen.append(request["operation"])
        assert peer == process_identity()
        return {"running": False}
    server = PermissionControlServer("environment", "host", handler)
    record = server.start()
    try:
        assert request_control(record, "environment", "host", "status") == {"running": False}
        with pytest.raises(Exception):
            request_control(record, "other", "host", "status")
        assert seen == ["status"]
        assert request_control(record, "environment", "host", "status") == {"running": False}
    finally:
        server.close()


def test_control_rejects_changed_process_identity_and_unknown_operation():
    server = PermissionControlServer("environment", "host", lambda *_: {})
    record = server.start()
    try:
        with pytest.raises(Exception, match="identity changed"):
            request_control({**record, "created": "different"}, "environment", "host", "status")
        with pytest.raises(ValueError):
            request_control(record, "environment", "host", "execute")
    finally:
        server.close()
