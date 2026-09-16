"""User-only permission commands. Models receive no management tool."""

import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from agent_adapters.storage.permissions import SQLitePermissions
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import ConfigurationError
from agent_contracts.permissions import PathPermission
from agent_subsystems.workspaces.permission_coordination import change_policy
from agent_subsystems.workspaces.permissions import authorize_path, freeze_policy
from .permission_host import remote_control
from .terminal_text import safe_text


def add_parser(commands):
    parser = commands.add_parser("permissions", help="长期权限；不调用模型")
    operations = parser.add_subparsers(dest="operation")
    operations.add_parser("list")
    check = operations.add_parser("check")
    check.add_argument("path")
    check.add_argument("--operation", dest="path_operation", choices=["read", "modify"],
                       required=True)
    for name in ("setup", "grant", "protect", "unprotect", "revoke", "enable", "disable"):
        operation = operations.add_parser(name)
        operation.add_argument("--revision", type=int)
        if name in {"grant", "protect", "unprotect", "revoke"}:
            operation.add_argument("path")
        if name in {"grant", "protect"}:
            operation.add_argument("--access", required=True,
                                   choices=["read", "modify"] if name == "grant" else ["read", "deny"])
        if name == "setup":
            operation.add_argument("--read-dir", action="append", default=[])
            operation.add_argument("--write-dir", action="append", default=[])
            operation.add_argument("--deny-dir", action="append", default=[])


def mandatory_rules(home):
    import os
    paths = [home, Path(__file__).resolve().parents[1], Path(sys.base_prefix), Path(sys.prefix)]
    if os.name == "nt":
        paths.append(Path(os.environ["ProgramFiles"]) / "AgentHub")
    # Trusted Python installations may themselves use a uv junction. Protect both
    # its lexical name and target; this does not allow aliases as business roots.
    protected = {Path(os.path.normcase(str(name)))
                 for path in paths for name in (path.absolute(), path.resolve())}
    return tuple(PathPermission(path, "deny") for path in sorted(protected))


def management_path(value):
    from agent_adapters.local.windows_permission_backend import canonical_ntfs
    return canonical_ntfs(Path(value))


def check_target(value):
    import os
    from agent_adapters.local.windows_permission_backend import canonical_ntfs
    path = Path(os.path.normcase(os.path.abspath(value)))
    parent = path
    while not parent.is_dir():
        if parent == parent.parent:
            raise ConfigurationError("目标没有可核实的本地目录。")
        parent = parent.parent
    canonical_ntfs(parent)
    if path.exists() and path.lstat().st_file_attributes & 0x400:
        raise ConfigurationError("检查目标是链接；请使用实际路径。")
    return path


def output(args, value):
    print(safe_text(json.dumps(value, ensure_ascii=False, default=str,
                               indent=None if args.json else 2)))


def task_view(controller):
    """Read-only UI projection; opening a draft must not upgrade or create state."""
    path = controller.home / "state.sqlite3"
    report = {"mode": controller.session["execution_mode"],
              "selection": controller.session["permission_selection"],
              "policy_revision": 0, "preparations": []}
    if not path.exists():
        return report
    store = SQLiteStore(path, readonly=True)
    try:
        if store.schema_version < 3:
            return {**report, "upgrade_required": True}
        authority = SQLitePermissions(store)
        policy = authority.load()
        report.update(policy_revision=policy.revision, enabled=policy.enabled,
                      grants=[asdict(rule) for rule in policy.grants],
                      protections=[asdict(rule) for rule in (*policy.protections, *policy.mandatory)])
        if store.schema_version >= 6:
            report["preparations"] = [{"id": row["id"], "state": row["state"]}
                                      for row in authority.preparations()]
        if controller.session["execution_mode"] == "windows_lpac":
            from agent_subsystems.workspaces.permission_records import selection_from_dict
            from agent_subsystems.workspaces.permissions import inheritable_roots
            snapshot = freeze_policy(policy, selection_from_dict(report["selection"]))
            report["effective"] = [asdict(rule) for rule in inheritable_roots(snapshot)]
        return report
    finally:
        store.close()


async def command(args, home):
    operation = args.operation or "list"
    readonly = operation in {"list", "check"}
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.json
    if not readonly and args.revision is None and not interactive:
        raise ConfigurationError("脚本修改权限必须提供 --revision；先运行 agenthub permissions。")
    path = home / "state.sqlite3"
    if not path.exists() and readonly:
        output(args, {"enabled": False, "revision": 0, "notice": "权限尚未启用"})
        return 0
    store = SQLiteStore(path, readonly=readonly)
    try:
        if store.schema_version < 3:
            output(args, {"enabled": False, "schema": store.schema_version,
                          "notice": "旧状态尚未绑定权限；请运行 agenthub state upgrade。"})
            return 0 if operation == "list" else 2
        authority = SQLitePermissions(store)
        current = authority.load()
        if operation == "list":
            pending = authority.pending() if store.schema_version >= 6 else None
            output(args, {"policy": asdict(current), "schema": store.schema_version,
                          "transition": {"id": pending["id"], "state": pending["state"]}
                          if pending else None})
            return 0
        if operation == "check":
            snapshot = freeze_policy(current)
            decision = authorize_path(snapshot, check_target(args.path), args.path_operation)
            output(args, asdict(decision))
            return 0 if decision.allowed else 2
        revision = current.revision if args.revision is None else args.revision
        target = replace(current, mandatory=mandatory_rules(home))
        if operation == "setup":
            reads, writes = list(args.read_dir), list(args.write_dir)
            if not reads and not writes:
                if not interactive:
                    raise ConfigurationError("setup 需要 --read-dir 或 --write-dir。")
                from .host import ask
                writes = [ask("可修改目录（请与归档目录分开）：")]
                archive = ask("只读目录（留空跳过）：").strip()
                if archive:
                    reads.append(archive)
            grants = {management_path(name): access
                      for access, names in (("read", reads), ("modify", writes)) for name in names}
            target = replace(target, grants=tuple(PathPermission(p, a) for p, a in grants.items()),
                             protections=tuple(PathPermission(management_path(p), "deny")
                                               for p in args.deny_dir), enabled=True)
        elif operation in {"enable", "disable"}:
            target = replace(target, enabled=operation == "enable")
        else:
            name = "grants" if operation in {"grant", "revoke"} else "protections"
            if operation in {"revoke", "unprotect"}:
                import os
                location = Path(os.path.normcase(os.path.abspath(args.path)))
                if not any(rule.path == location for rule in getattr(target, name)):
                    raise ConfigurationError("没有对应的已保存规则；请先查看 permissions。")
            else:
                location = management_path(args.path)
            rules = [rule for rule in getattr(target, name) if rule.path != location]
            if operation in {"grant", "protect"}:
                rules.append(PathPermission(location, args.access))
            target = replace(target, **{name: tuple(rules)})
        freeze_policy(replace(target, enabled=True))
        if target.enabled:
            from .sandbox import require_setup
            require_setup(home)
        if interactive:
            from .host import ask
            output(args, {"target": asdict(target), "notice": "不支持可写目录内的只读例外。"})
            if ask("保存长期策略？[y/N] ").lower() != "y":
                return 0
        async def control(host, verb, identifier):
            return await remote_control(authority, host, verb, identifier)
        result = await change_policy(authority, target, revision, control)
        output(args, {"saved": True, "revision": result.revision, "enabled": result.enabled,
                      "notice": "后续 Run 使用新策略；实际准备在本 CLI 首次执行时完成。"})
        return 0
    finally:
        store.close()
