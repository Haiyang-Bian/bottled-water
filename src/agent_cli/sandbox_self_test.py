"""No-model self-test on a fresh, owned NTFS tree; records real OS refusals."""

import json
import os
from pathlib import Path
import time
from types import SimpleNamespace
from uuid import uuid4

from agent_adapters.local.restricted import WindowsRestrictedDriver
from agent_adapters.storage.permissions import SQLitePermissions
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.execution import ExecutionContext, ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_contracts.permissions import StandingPermissionPolicy, PathPermission
from agent_subsystems.workspaces.permissions import freeze_policy
from agent_subsystems.workspaces.permission_preparation import PreparedPolicy
from agent_adapters.local.windows_permission_backend import WindowsPermissionBackend
from .sandbox import require_setup


async def run(home):
    value, manifest = require_setup(home)
    output = home / "sandbox" / "self-tests" / uuid4().hex
    output.mkdir(parents=True)
    tree = output / "Tree"
    roots = {}
    for name in ("Work", "Archive", "Private"):
        path = tree / name
        path.mkdir(parents=True)
        (path / "sample.txt").write_text(name, encoding="utf-8")
        roots[name] = Path(os.path.normcase(str(path)))
    store = SQLiteStore(output / "state.sqlite3")
    authority = SQLitePermissions(store)
    policy = StandingPermissionPolicy(store.environment.environment_id, "local", 0, (
        PathPermission(roots["Work"], "modify"), PathPermission(roots["Archive"], "read"),
    ), enabled=True)
    transition, policy, _ = authority.begin(policy, 0)
    authority.transition(transition, "retiring")
    authority.commit(transition)
    from agent_adapters.local.windows_permission_ipc import process_identity
    host = uuid4().hex
    authority.host(host, process_identity())
    snapshot = freeze_policy(policy)
    backend = WindowsPermissionBackend(authority, host, manifest)
    prepared = PreparedPolicy(snapshot, manifest.digest, backend)
    report = {"passed": False, "checks": [], "cleanup": False,
              "dependency_digest": manifest.digest, "policy_digest": snapshot.digest}
    driver = None
    try:
        prepared.prepare()
        private = output / "Runs"
        private.mkdir()
        driver = WindowsRestrictedDriver(prepared, value["bundle"], private,
                                         value["executables"], namespace_experiment=value["namespace"])
        context = ExecutionContext(uuid4().hex, "self-test", "local",
            ResourceGrant(WorkspaceSpec(tuple(roots.values())[:2]), frozenset({"files", "process"}),
                          "windows_lpac", snapshot), time.monotonic() + 240,
            SimpleNamespace(raise_if_cancelled=lambda: None),
            SimpleNamespace(require_valid=lambda: None), ExecutionLocation(roots["Work"]))
        await driver.prepare(context)
        content = await driver.invoke("read", {"path": str(roots["Archive"] / "sample.txt")}, context)
        report["checks"].append({"name": "archive_read", "result": content})
        await driver.invoke("write", {"path": "written.txt", "content": "OK", "expected_hash": "new"},
                            context)
        script = (
            "from pathlib import Path\n"
            f"work=Path({str(roots['Work'])!r}); archive=Path({str(roots['Archive'])!r}); "
            f"private=Path({str(roots['Private'])!r})\n"
            "assert (work/'written.txt').read_text()=='OK'\n"
            "assert (archive/'sample.txt').read_text()=='Archive'\n"
            "for path, mode in [(archive/'sample.txt','w'),(private/'sample.txt','r')]:\n"
            " try: path.open(mode).close()\n"
            " except PermissionError: print('OS DENIED', mode)\n"
            " else: raise AssertionError('permission bypass')\n"
        )
        result = await driver.run([value["executables"]["python"], "-B", "-c", script],
                                  roots["Work"], timeout=30, context=context)
        report["checks"].append({"name": "python_os_denials", "result": result})
        assert result["exit_code"] == 0 and result["stdout"].count("OS DENIED") == 2
        for kind, argv in (
            ("pwsh", ["-NoProfile", "-NonInteractive", "-Command", "Get-Content written.txt"]),
            ("git", ["--version"]), ("uv", ["--version"]),
        ):
            result = await driver.run([value["executables"][kind], *argv], roots["Work"],
                                      timeout=30, context=context)
            report["checks"].append({"name": kind, "result": result})
            assert result["exit_code"] == 0
        report["passed"] = True
    except Exception as exc:
        report["error"] = str(exc)
    finally:
        try:
            if driver:
                await driver.aclose()
            prepared.retire()
            report["cleanup"] = True
        except Exception as exc:
            report.update(passed=False, cleanup_error=str(exc))
        store.close()
        report["evidence"] = str(output / "report.json")
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=True, indent=2),
                                           encoding="utf-8")
    return report
