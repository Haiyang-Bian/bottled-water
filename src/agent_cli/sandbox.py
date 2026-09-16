"""Explicit sandbox installation/diagnostics. Ordinary Runs never request elevation."""

import asyncio
import json
import os
import platform
from pathlib import Path
import sys
from uuid import uuid4

from agent_adapters.local.dependencies import DependencyManifest
from agent_adapters.local.sandbox_components import (
    build_bundle, read_setup, selected_tools, save_setup,
)
from agent_adapters.storage.permissions import SQLitePermissions, PermissionBusyError
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import ConfigurationError


def add_parser(commands):
    operations = commands.add_parser("sandbox").add_subparsers(dest="operation", required=True)
    setup = operations.add_parser("setup")
    setup.add_argument("--apply", action="store_true", help="Apply the displayed fixed initialization")
    operations.add_parser("doctor")
    operations.add_parser("self-test")
    operations.add_parser("repair")
    uninstall = operations.add_parser("uninstall")
    uninstall.add_argument("--apply", action="store_true")


def require_platform():
    if (os.name != "nt" or platform.machine().upper() != "AMD64"
            or sys.getwindowsversion().build < 22000):
        raise ConfigurationError("本阶段受限驱动仅面向 Windows 11 x64。")


def require_setup(home):
    require_platform()
    from agent_adapters.local.sandbox_admin import verify_component
    from agent_adapters.local.windows_namespace import TARGETS, entries_for_sid, open_target, read_acl
    from agent_adapters.local.windows_lpac import system_capability_sids
    value, manifest = read_setup(home)
    root = Path(value["component"])
    verify_component(root, value["component_hashes"])
    component = json.loads((root / "component.json").read_text(encoding="utf-8"))
    if component["namespace"] != value["namespace"]:
        raise ConfigurationError("初始化组件身份不一致；请运行 sandbox setup。")
    sid = system_capability_sids("AgentHub.Probe.Namespace." + value["namespace"])[0]
    for target in TARGETS:
        handle = open_target(target, write=False)
        try:
            entries = entries_for_sid(read_acl(handle), sid)
            if len(entries) != 1 or entries[0][1][:2] != ((0, 0), target.mask):
                raise ConfigurationError("系统初始化已失效；请显式运行 sandbox setup --apply。")
        finally:
            handle.Close()
    return value, manifest


async def command(args, home):
    if args.operation == "doctor":
        return await _command(args, home)
    from agent_adapters.storage.session_lock import SessionLock
    with SessionLock(home / "locks", "__sandbox_management__"):
        return await _command(args, home)


async def _command(args, home):
    from .permissions import output
    operation = args.operation
    if operation == "doctor":
        try:
            value, manifest = require_setup(home)
            report = {"ready": True, "dependency_digest": manifest.digest,
                      "bundle": value["bundle"], "tools": value["executables"],
                      "component": value["component"], "network": "deny",
                      "os_build": sys.getwindowsversion().build}
        except Exception as exc:
            report = {"ready": False, "reason": str(exc), "repair": "agenthub sandbox setup"}
            try:
                value, _ = read_setup(home, allow_pending=True)
                report["installation_state"] = value["state"]
                ledger = Path(value["component"]) / "initialization.json"
                if ledger.exists():
                    saved = json.loads(ledger.read_text(encoding="utf-8"))
                    report["initializer"] = {key: saved[key] for key in ("state", "error", "detail")
                                              if key in saved}
                from agent_adapters.local.windows_namespace import (
                    TARGETS, capability_name, entries_for_sid, open_target, read_acl,
                )
                from agent_adapters.local.windows_lpac import system_capability_sids
                sid = system_capability_sids(capability_name(value["namespace"]))[0]
                observed = []
                for target in TARGETS:
                    handle = open_target(target, write=False)
                    try:
                        observed.append({"path": target.path,
                                         "managed_entries": len(entries_for_sid(read_acl(handle), sid))})
                    finally:
                        handle.Close()
                report["system_query_permissions"] = observed
            except (OSError, ValueError, KeyError, ConfigurationError):
                pass
        path = home / "state.sqlite3"
        if path.exists():
            store = SQLiteStore(path, readonly=True)
            try:
                report["schema"] = store.schema_version
                if store.schema_version >= 6:
                    report["preparations"] = [{"id": r["id"], "state": r["state"]}
                                               for r in SQLitePermissions(store).preparations()]
            finally:
                store.close()
        output(args, report)
        return 0 if report["ready"] else 2
    if operation == "setup":
        require_platform()
        from agent_adapters.local.sandbox_install import (
            stage_component, install_component, invoke_component,
        )
        from agent_adapters.local.windows_namespace import TARGETS
        from dataclasses import asdict
        sources = selected_tools()
        output(args, {"sources": sources, "fixed_objects": [asdict(t) for t in TARGETS],
                      "component_root": str(Path(os.environ["ProgramFiles"]) / "AgentHub/Sandbox"),
                      "startup_task": False, "business_acl_changes": False})
        apply = args.apply
        if not apply and sys.stdin.isatty() and sys.stdout.isatty() and not args.json:
            from .host import ask
            apply = ask("准备以上固定组件并请求管理员初始化？[y/N] ").lower() == "y"
        if not apply:
            return 0
        try:
            value, _ = require_setup(home)
        except Exception:
            value = None
        if value:
            output(args, {"ready": True, "changed": False})
            return 0
        existing = home / "sandbox/setup.json"
        if existing.exists():
            value, _ = read_setup(home, allow_pending=True)
            if value["state"] == "preparing" and not Path(value["component"]).exists():
                # No protected path exists yet: retry the recorded installation,
                # retaining the identity and hashes of the failed attempt.
                await asyncio.to_thread(install_component, Path(value["staging"]),
                                        Path(value["component"]), value["component_hashes"])
            else:
                await asyncio.to_thread(invoke_component, Path(value["component"]), "setup",
                                        value["component_hashes"])
            save_setup(home, {**value, "state": "ready"})
            require_setup(home)
            output(args, {"ready": True, "initialized": True})
            return 0
        identifier = uuid4().hex
        staging = home / "sandbox" / "staging" / identifier
        component = Path(os.environ["ProgramFiles"]) / "AgentHub/Sandbox" / identifier
        bundle = home / "sandbox" / "tools" / identifier
        executables = build_bundle(bundle, sources)
        manifest = DependencyManifest.capture(bundle)
        hashes = stage_component(staging, identifier)
        output(args, {"dependency_digest": manifest.digest, "component_files": len(hashes),
                      "component_hashes": hashes})
        value = {"bundle": str(bundle), "manifest": manifest.encoded, "digest": manifest.digest,
                 "component": str(component), "namespace": identifier, "executables": executables,
                 "software_mapping": {}, "component_hashes": hashes, "state": "preparing",
                 "staging": str(staging)}
        save_setup(home, value)  # Recovery identity must precede the administrator operation.
        await asyncio.to_thread(install_component, staging, component, hashes)
        (home / "sandbox" / "runs").mkdir(parents=True, exist_ok=True)
        save_setup(home, {**value, "state": "ready"})
        require_setup(home)
        output(args, {"ready": True, "dependency_digest": manifest.digest})
        return 0
    if operation == "self-test":
        from .sandbox_self_test import run
        report = await run(home)
        output(args, report)
        return 0 if report["passed"] else 1
    value, manifest = read_setup(home)
    store = SQLiteStore(home / "state.sqlite3")
    try:
        if operation == "repair":
            from .permission_host import recover_preparations
            repaired = recover_preparations(store, manifest)
            output(args, {"repaired": repaired, "policy_changed": False})
            return 0
        authority = SQLitePermissions(store)
        if authority.load().enabled or authority.preparations():
            raise PermissionBusyError("先禁用长期策略并核实所有宿主清理，再卸载初始化。")
        output(args, {"remove_initialization": value["component"],
                      "retained": "用户状态、历史及隔离工具文件；不会删除用户数据。"})
        if args.apply:
            from agent_adapters.local.sandbox_install import invoke_component
            await asyncio.to_thread(invoke_component, Path(value["component"]), "remove",
                                    value["component_hashes"])
            save_setup(home, {**value, "state": "removed"})
            authority.audit("sandbox.uninstalled", {"component": value["component"]})
        return 0
    finally:
        store.close()
