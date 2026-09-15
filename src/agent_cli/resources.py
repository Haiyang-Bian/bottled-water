"""User resource/software management; never constructs a provider or a chat Run."""

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict, replace
from pathlib import Path

from agent_adapters.local.resources import (
    LocalSoftware,
    discover,
    index_directory,
    management_operation,
    probe,
)
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.storage.resources import SQLiteResources
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.execution import ExecutionLocation, WorkspaceSpec
from agent_contracts.resources import ResourceRevision, ResourceSource
from agent_subsystems.workspaces.paths import effective_roots, resolve_resource
from agent_subsystems.workspaces.resources import KINDS
from .privacy import display_redactor
from .terminal_text import safe_text


def add_parser(commands):
    for family in ("resources", "software"):
        root = commands.add_parser(
            family, help="Manage saved metadata; file grants remain separate"
        )
        sub = root.add_subparsers(dest="resource_operation")
        names = ["list", "search", "show", "add", "verify", "disable", "enable"]
        names += (
            ["discover"]
            if family == "software"
            else ["edit", "relocate", "index", "process", "rebuild"]
        )
        for name in names:
            cmd = sub.add_parser(name)
            if name in {"show", "verify", "disable", "enable", "edit", "relocate"}:
                cmd.add_argument("id", nargs="?")
                cmd.add_argument("--revision", type=int)
            if name in {"list", "search"}:
                cmd.add_argument("query", nargs="?", default="")
                cmd.add_argument("--offset", type=int, default=0)
                cmd.add_argument("--limit", type=int, default=20)
            if name in {"add", "edit", "relocate", "discover"}:
                cmd.add_argument("--path")
            if name in {"add", "edit"}:
                cmd.add_argument("--name")
                cmd.add_argument("--alias", action="append")
            if name in {"add", "edit", "discover", "verify"}:
                cmd.add_argument(
                    "--kind",
                    choices=sorted(
                        {"python", "uv", "git"} if family == "software" else KINDS - {"software"}
                    ),
                )
            if name == "index":
                cmd.add_argument("path")
                cmd.add_argument("--max-entries", type=int, default=10000)
                cmd.add_argument("--timeout", type=float, default=30)
                cmd.add_argument("--include-ignored", action="store_true")


def emit(args, redactor, value):
    data = redactor.dumps(value)
    if args.json:
        print(data)
    else:
        rows = json.loads(data)
        if (
            sys.stdout.isatty()
            and not getattr(args, "plain", False)
            and isinstance(rows, list)
            and rows
            and all("content" in r for r in rows)
        ):
            from rich.console import Console
            from rich.table import Table
            from rich.text import Text

            table = Table(title="资源目录 · 保存的信息不代表当前文件状态", expand=True)
            for title in ("名称", "类型 / 状态", "修订", "位置"):
                table.add_column(title)
            for row in rows:
                table.add_row(
                    Text(safe_text(row["content"]["name"])),
                    Text(row["content"]["kind"] + " / " + row["status"]),
                    str(row["revision"]),
                    Text(safe_text(row["content"]["path"])),
                )
            Console(no_color=args.no_color).print(table)
        else:
            print(safe_text(json.dumps(rows, ensure_ascii=False, indent=2)))


async def choose(rows, args):
    from .selection import Choice, Selector

    choices = [
        Choice(
            r.id,
            f"{r.content.name} · {r.status} · v{r.revision}",
            safe_text(f"{r.content.path}\n{r.id}"),
        )
        for r in rows
    ]
    return await Selector(choices, "资源 · 元数据不授予文件访问权", color=not args.no_color).run()


def authorized(store, cwd, roots=None):
    saved = (
        roots if roots is not None else [r[0] for r in store.db.execute("SELECT path FROM trusted")]
    )
    available, _ = effective_roots(saved, store.is_trusted)
    return WorkspaceSpec(available), ExecutionLocation(Path(cwd))


async def command(args, home, *, cwd=None, roots=None, scope_id=None, interactive_override=False):
    operation = args.resource_operation or "list"
    family = args.command
    cwd = Path(cwd or Path.cwd())
    interactive = (
        interactive_override or sys.stdin.isatty() and sys.stdout.isatty()
    ) and not args.json
    args.no_color = (
        args.no_color or getattr(args, "plain", False) or bool(os.environ.get("NO_COLOR"))
    )
    redactor = display_redactor(home)
    if operation == "discover":
        if not args.kind:
            raise ConfigurationError("Specify --kind python|uv|git")
        emit(args, redactor, discover(args.kind, cwd, args.path))
        return 0
    readonly = operation in {"list", "search", "show"}
    path = home / "state.sqlite3"
    if readonly and not path.exists():
        emit(args, redactor, {"enabled": False, "notice": "Resource catalog not initialized"})
        return 0
    store = SQLiteStore(path, redactor, readonly=readonly)
    driver = None
    try:
        if store.schema_version < 5:
            emit(args, redactor, {"enabled": False, "notice": "Run agenthub state upgrade"})
            return 0
        catalog = SQLiteResources(store)
        access = catalog.access(scope_id=scope_id)
        kind_filter = "software" if family == "software" else None
        if operation in {"list", "search"}:
            rows = catalog.search(
                access,
                getattr(args, "query", ""),
                management=True,
                kind=kind_filter,
                offset=getattr(args, "offset", 0),
                limit=getattr(args, "limit", 20),
            )
            emit(args, redactor, [asdict(r) for r in rows])
            if interactive and operation == "list" and rows:
                selected = await choose(rows, args)
                if selected:
                    emit(args, redactor, detail(catalog, access, selected, family))
            return 0
        if operation in {"process", "rebuild"}:
            value = catalog.process(access) if operation == "process" else catalog.rebuild(access)
            emit(args, redactor, value or {"rebuilt": True})
            return 0
        if operation == "index":
            from agent_adapters.local.files import LocalFiles
            from agent_adapters.local.tools import read_git_index

            if not 0 < args.timeout <= 3600:
                raise ConfigurationError("Index timeout must be in (0, 3600]")
            workspace, location = authorized(store, cwd, roots)
            directory = resolve_resource(workspace, location, args.path, directory=True)
            driver = LocalProcessDriver(redactor)
            context = management_operation(args.timeout)

            async def read_index(target):
                return await read_git_index(driver, target, context)

            files = LocalFiles(workspace, location, index_reader=read_index)
            value = await index_directory(
                files,
                directory,
                catalog,
                access,
                context,
                max_entries=args.max_entries,
                include_ignored=args.include_ignored,
            )
            emit(args, redactor, value)
            return 0
        identifier = getattr(args, "id", None)
        record = None
        if operation != "add":
            if not identifier:
                if not interactive:
                    raise ConfigurationError(
                        "Explicit ID required; use agenthub --json resources list"
                    )
                from .memory import all_pages

                identifier = await choose(
                    all_pages(
                        lambda **kw: catalog.search(access, management=True, kind=kind_filter, **kw)
                    ),
                    args,
                )
                if identifier is None:
                    return 0
            record = catalog.read(access, identifier, management=True)
            if operation == "show":
                emit(args, redactor, detail(catalog, access, identifier, family))
                return 0
            revision = getattr(args, "revision", None)
            if revision is None:
                if not interactive:
                    raise ConfigurationError(
                        "Modifications require --revision N from the saved record"
                    )
                revision = record.revision
            if revision != record.revision:
                raise OperationError("resource_conflict", "Resource changed; reread its revision")
        if operation in {"disable", "enable"} and not (
            family == "software" and operation == "enable"
        ):
            result = catalog.set_status(
                access, identifier, revision, "disabled" if operation == "disable" else "active"
            )
        elif family == "software" and operation in {"add", "verify", "enable"}:
            software_kind = getattr(args, "kind", None)
            if operation == "add":
                if not software_kind:
                    raise ConfigurationError("Specify --kind python|uv|git")
                executable = args.path
                if not executable and interactive:
                    from .selection import Choice, Selector

                    rows = discover(software_kind, cwd)
                    executable = await Selector(
                        [Choice(r["path"], safe_text(r["path"]), r["notice"]) for r in rows],
                        "选择并验证软件",
                        color=not args.no_color,
                    ).run()
                    if executable is None:
                        return 0
                if not executable:
                    raise ConfigurationError("Specify --path using a discovered real executable")
                record = catalog.save(
                    access,
                    ResourceRevision(
                        args.name or software_kind,
                        str(cwd / executable),
                        "software",
                        tuple(args.alias or ()),
                    ),
                )
                if record.status == "disabled":
                    raise ConfigurationError(
                        "Existing software is disabled; use software enable ID --revision N"
                    )
                revision = record.revision
            else:
                if not software_kind:
                    _, old_cfg = catalog.software(access, identifier, management=True)
                    software_kind = old_cfg.kind
            driver = LocalProcessDriver(redactor)
            context = management_operation(10)
            try:
                cfg = await LocalSoftware(driver).verify(record, software_kind, cwd, context)
            except BaseException as exc:
                with store.transaction():
                    catalog._event(
                        access,
                        record.id,
                        "software_verification_failed",
                        record.revision,
                        {"operation_id": context.operation_id, "error": type(exc).__name__},
                    )
                raise
            result = catalog.save_software(access, record.id, revision, cfg)
        elif operation in {"add", "edit", "relocate"}:
            if interactive:
                from prompt_toolkit import PromptSession

                prompt = PromptSession()
                if operation in {"add", "relocate"} and not args.path:
                    args.path = await prompt.prompt_async("位置：")
                if operation == "add" and not args.name:
                    args.name = await prompt.prompt_async("名称：")
                if operation == "edit" and not any((args.name, args.path, args.kind, args.alias)):
                    args.name = await prompt.prompt_async("名称：", default=record.content.name)
                    args.kind = await prompt.prompt_async("类型：", default=record.content.kind)
                    aliases = await prompt.prompt_async(
                        "别名（逗号分隔）：", default=",".join(record.content.aliases)
                    )
                    args.alias = [a.strip() for a in aliases.split(",") if a.strip()]
            if operation in {"add", "relocate"} and not args.path:
                raise ConfigurationError("Specify --path")
            if operation == "add" and not args.name:
                raise ConfigurationError("Specify --name")
            value = record.content if record else ResourceRevision(args.name, str(cwd / args.path))
            fields = {
                k: getattr(args, k) for k in ("name", "kind") if getattr(args, k, None) is not None
            }
            if getattr(args, "alias", None) is not None:
                fields["aliases"] = tuple(args.alias)
            if args.path:
                fields["path"] = str(cwd / args.path)
            value = replace(value, **fields)
            result = (
                catalog.revise(access, identifier, revision, value)
                if record
                else catalog.save(access, value)
            )
        elif operation == "verify":
            if record.status != "active":
                raise OperationError("resource_disabled", "Enable this resource before verifying it")
            workspace, location = authorized(store, cwd, roots)
            target = resolve_resource(workspace, location, record.content.path)
            context = management_operation()
            facts = await probe(target, context)
            with store.transaction():
                if catalog.read(access, identifier, management=True).revision != revision:
                    raise OperationError(
                        "resource_conflict", "Resource changed during verification"
                    )
                result = catalog._observe(
                    access,
                    str(target),
                    facts,
                    ResourceSource("verification", operation_id=context.operation_id),
                )
                catalog._event(
                    access, identifier, "verified", revision, {"operation_id": context.operation_id}
                )
        else:
            raise ConfigurationError("Unknown resource operation")
        emit(args, redactor, asdict(result))
        return 0
    finally:
        if driver:
            await driver.aclose()
        store.close()


def detail(catalog, access, identifier, family):
    value = {
        "record": asdict(catalog.read(access, identifier, management=True)),
        "events": catalog.audit(access, identifier),
        "tasks": catalog.links(access, identifier),
        "notice": "Saved metadata; current filesystem state has not been checked",
    }
    if family == "software":
        try:
            value["software"] = asdict(catalog.software(access, identifier, management=True)[1])
        except OperationError:
            value["software"] = {"enabled": False, "notice": "Not verified"}
    return value


async def interactive_command(text, controller, args):
    root = argparse.ArgumentParser(prog=text.split()[0], exit_on_error=False)
    add_parser(root.add_subparsers(dest="command"))
    try:
        tokens = shlex.split(text[1:], posix=False)
        tokens = [
            t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in (chr(34), chr(39)) else t
            for t in tokens
        ]
        parsed = root.parse_args(tokens)
    except (SystemExit, argparse.ArgumentError, ValueError):
        return
    parsed.json, parsed.no_color, parsed.plain = False, args.no_color, args.plain
    await command(
        parsed,
        controller.home,
        cwd=controller.session["cwd"],
        roots=controller.session["granted_roots"],
        scope_id=controller.session["id"] if controller.lock else None,
        interactive_override=True,
    )
