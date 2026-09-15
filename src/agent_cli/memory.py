"""User-owned memory management. No model client or task creation is needed."""

import argparse
import json
import os
import shlex
import sys
from dataclasses import asdict, replace
from pathlib import Path

from agent_adapters.storage.memory import SQLiteMemory
from agent_adapters.storage.sqlite import SQLiteStore
from agent_contracts.errors import ConfigurationError
from agent_contracts.memory import MemoryRevision
from .privacy import display_redactor
from .terminal_text import safe_text

BOUNDARY = (
    "采纳/保存后，本环境默认助手可跨任务使用此知识，但不会取得来源文件权限。"
    "撤销目录信任不会撤销已批准记忆；可另行停用或遗忘。"
)
FORGET = "遗忘将移除可召回正文和索引，并抑制旧来源再生；原会话、Run 日志和备份仍保留。"
READ_ONLY = {"list", "search", "show", "candidates", "used"}


def add_parser(commands):
    root = commands.add_parser(
        "memory", help="Manage approved cross-task knowledge; no model needed"
    )
    sub = root.add_subparsers(dest="memory_operation")
    for name in (
        "list",
        "search",
        "show",
        "candidates",
        "used",
        "add",
        "edit",
        "disable",
        "enable",
        "forget",
        "adopt",
        "reject",
        "process",
        "rebuild",
    ):
        cmd = sub.add_parser(name)
        if name in {"show", "edit", "disable", "enable", "forget", "adopt", "reject"}:
            cmd.add_argument("id", nargs="?")
            cmd.add_argument("--revision", type=int)
        if name in {"list", "search", "candidates"}:
            cmd.add_argument("query", nargs="?", default="")
            cmd.add_argument("--offset", type=int, default=0)
            cmd.add_argument("--limit", type=int, default=20)
        if name in {"add", "edit", "adopt"}:
            cmd.add_argument("--title")
            cmd.add_argument("--body")
            cmd.add_argument(
                "--kind", choices=["preference", "environment", "decision", "experience"]
            )
            cmd.add_argument("--directory", help="Applicability only; does not grant file access")
            cmd.add_argument("--global", dest="global_scope", action="store_true")
            cmd.add_argument("--basic", action=argparse.BooleanOptionalAction, default=None)
            cmd.add_argument("--tag", action="append")
            cmd.add_argument("--alias", action="append")
        if name == "forget":
            cmd.add_argument("--yes", action="store_true", help=FORGET)
        if name == "used":
            cmd.add_argument("--run")
        if name == "process":
            cmd.add_argument("--limit", type=int, default=20)
    return root


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
        ):
            from rich.console import Console
            from rich.table import Table
            from rich.text import Text

            if all(isinstance(r, dict) and "content" in r for r in rows):
                table = Table(title="记忆 · 来源与修订可在详情查看", expand=True)
                for name in ("标题", "类型 / 状态", "修订", "适用范围"):
                    table.add_column(name)
                for row in rows:
                    c = row["content"]
                    table.add_row(
                        Text(safe_text(c["title"])),
                        Text(c["kind"] + " / " + row["status"]),
                        str(row["revision"]),
                        Text(safe_text(c["directory"] or "全局")),
                    )
                Console(no_color=args.no_color).print(table)
                return
        print(safe_text(json.dumps(rows, ensure_ascii=False, indent=2)))


async def choose(rows, title, *, color=True):
    from .selection import Choice, Selector

    choices = [
        Choice(
            r.id,
            f"{r.content.title} · {r.status} · v{r.revision}",
            safe_text(
                f"{r.content.body[:240]}\nID：{r.id} · 来源："
                + ", ".join(s.kind for s in r.sources)
            ),
        )
        for r in rows
    ]
    return await Selector(choices, title, color=color).run()


def all_pages(reader):
    rows, offset = [], 0
    while True:
        page = reader(offset=offset, limit=20)
        rows.extend(page)
        if len(page) < 20:
            return rows
        offset += 20


async def form(args, current=None, *, cwd=None, redactor=None):
    from prompt_toolkit import PromptSession

    prompt = PromptSession()
    content = current or MemoryRevision("", "")
    title = args.title
    body = args.body
    if title is None:
        title = await prompt.prompt_async("标题：", default=content.title)
    if body is None:
        body = await prompt.prompt_async("正文（最多 2000 字符）：", default=content.body)
    kind = args.kind or await prompt.prompt_async(
        "类型 preference/environment/decision/experience：", default=content.kind
    )
    directory = args.directory
    if directory is None and not args.global_scope:
        directory = await prompt.prompt_async(
            "适用目录（空白=全局）：", default=content.directory or ""
        )
    basic = args.basic
    if basic is None:
        answer = await prompt.prompt_async(
            "作为基础资料？[y/N] ", default="y" if content.basic or kind == "preference" else "n"
        )
        basic = answer.lower() in {"y", "yes", "是"}
    result = content_values(
        args,
        replace(
            content, title=title, body=body, kind=kind, directory=directory or None, basic=basic
        ),
        cwd,
    )
    data = asdict(result)
    if redactor:
        data = redactor.value(data)
    print(safe_text(json.dumps(data, ensure_ascii=False, indent=2)))
    print(BOUNDARY)
    answer = await prompt.prompt_async("确认保存/采纳？[y/N] ")
    return result if answer.lower() in {"y", "yes", "是"} else None


def content_values(args, current=None, cwd=None):
    content = current or MemoryRevision("", "")
    fields = {
        key: getattr(args, key)
        for key in ("title", "body", "kind", "basic")
        if getattr(args, key, None) is not None
    }
    for option, field in (("tag", "tags"), ("alias", "aliases")):
        if getattr(args, option, None) is not None:
            fields[field] = tuple(getattr(args, option))
    if args.directory:
        path = Path(args.directory).expanduser()
        fields["directory"] = str((Path(cwd or Path.cwd()) / path).resolve())
    elif args.global_scope:
        fields["directory"] = None
    content = replace(content, **fields)
    if content.directory:
        content = replace(
            content,
            directory=str(
                (Path(cwd or Path.cwd()) / Path(content.directory).expanduser()).resolve()
            ),
        )
    if current is None and args.basic is None:
        content = replace(content, basic=content.kind == "preference" and not content.directory)
    return content


async def command(args, home, *, scope_id=None, cwd=None, interactive_override=False):
    operation = args.memory_operation or "list"
    interactive = (
        interactive_override or sys.stdin.isatty() and sys.stdout.isatty()
    ) and not args.json
    args.no_color = (
        args.no_color or getattr(args, "plain", False) or bool(os.environ.get("NO_COLOR"))
    )
    readonly = operation in READ_ONLY
    path = home / "state.sqlite3"
    if readonly and not path.exists():
        emit(
            args,
            display_redactor(home),
            {"enabled": False, "notice": "Memory not enabled; save or upgrade to initialize"},
        )
        return 0
    redactor = display_redactor(home)
    store = SQLiteStore(path, redactor, readonly=readonly)
    try:
        if store.schema_version < 4:
            emit(
                args,
                redactor,
                {"enabled": False, "notice": "Run agenthub state upgrade to enable memory"},
            )
            return 0
        memory = SQLiteMemory(store)
        access = memory.access(scope_id=scope_id)
        if operation in {"list", "search"}:
            rows = memory.search(
                access,
                getattr(args, "query", ""),
                offset=getattr(args, "offset", 0),
                limit=getattr(args, "limit", 20),
                cwd=cwd,
                management=True,
            )
            emit(args, redactor, rows)
            if interactive and rows:
                identifier = await choose(
                    rows, "选择记忆 · 查看来源与修订", color=not args.no_color
                )
                if identifier:
                    from .selection import pager

                    await pager(redactor.dumps(memory.read(access, identifier, management=True)))
            return 0
        if operation == "used":
            emit(args, redactor, memory.used(access, getattr(args, "run", None)))
            return 0
        if operation in {"process", "rebuild"}:
            result = (
                memory.process(access, args.limit)
                if operation == "process"
                else memory.rebuild(access)
            )
            emit(args, redactor, result)
            return 0
        if operation == "candidates":
            rows = memory.candidates(access, offset=args.offset, limit=args.limit)
            emit(args, redactor, rows)
            if interactive:
                await review_candidates(memory, access, rows, args, redactor)
            return 0
        identifier = getattr(args, "id", None)
        if operation != "add" and not identifier:
            if not interactive:
                raise ConfigurationError(
                    "Explicit memory ID required; use agenthub --json memory list"
                )
            reader = memory.candidates if operation in {"adopt", "reject"} else memory.search
            rows = all_pages(
                lambda **page: reader(
                    access,
                    **page,
                    **({} if operation in {"adopt", "reject"} else {"management": True}),
                )
            )
            identifier = await choose(rows, "选择记忆", color=not args.no_color)
            if not identifier:
                return 0
        if operation in {"adopt", "reject"}:
            item = memory.candidate(access, identifier)
        elif operation != "add":
            item = memory.read(
                access,
                identifier,
                management=True,
                revision=args.revision if operation == "show" else None,
            )
        else:
            item = None
        if operation == "show":
            emit(
                args,
                redactor,
                {**asdict(item), "management_events": memory.audit(access, identifier)},
            )
            return 0
        revision = getattr(args, "revision", None)
        if item and revision is None:
            if not interactive:
                raise ConfigurationError("Modification requires --revision from the current record")
            revision = item.revision
        if operation in {"add", "edit", "adopt"}:
            if interactive:
                content = await form(
                    args, item.content if item else None, cwd=cwd, redactor=redactor
                )
                if content is None:
                    return 0
            else:
                if operation == "add" and (not args.title or not args.body):
                    raise ConfigurationError("memory add requires --title and --body")
                content = content_values(args, item.content if item else None, cwd)
            if operation == "add":
                result = memory.save(access, content)
            elif operation == "edit":
                result = memory.revise(access, identifier, revision, content)
            else:
                result = memory.decide(access, identifier, revision, adopt=True, content=content)
        elif operation == "reject":
            result = memory.decide(access, identifier, revision)
        else:
            if operation == "forget" and not args.yes:
                if not interactive:
                    raise ConfigurationError(FORGET + " 请显式传入 --yes。")
                from prompt_toolkit import PromptSession

                print(FORGET)
                if (await PromptSession().prompt_async("确认遗忘？[y/N] ")).lower() not in {
                    "y",
                    "yes",
                    "是",
                }:
                    return 0
            result = memory.set_status(
                access,
                identifier,
                revision,
                {"disable": "disabled", "enable": "active", "forget": "forgotten"}[operation],
            )
        emit(args, redactor, result)
        if not args.json:
            print(FORGET if operation == "forget" else BOUNDARY)
        return 0
    finally:
        store.close()


async def review_candidates(memory, access, rows, args, redactor):
    from .selection import Choice, Selector

    while rows:
        identifier = await choose(rows, "记忆候选 · 采纳后跨任务生效", color=not args.no_color)
        if not identifier:
            return
        item = memory.candidate(access, identifier)
        print(safe_text(redactor.dumps(item)))
        print(BOUNDARY)
        action = await Selector(
            [
                Choice("adopt", "采纳", BOUNDARY),
                Choice("edit", "修改后采纳", "保持原来源类别，仍须通过来源校验"),
                Choice("reject", "拒绝", "候选不会生效"),
                Choice("all", "采纳本页全部已校验候选", BOUNDARY),
            ],
            "选择操作 · Esc 返回",
            color=not args.no_color,
        ).run()
        if not action:
            continue
        if action == "edit":
            namespace = argparse.Namespace(
                title=None,
                body=None,
                kind=None,
                basic=None,
                directory=None,
                global_scope=False,
                tag=None,
                alias=None,
            )
            content = await form(namespace, item.content, redactor=redactor)
            if content is None:
                continue
            result = decide_from_view(
                memory, access, identifier, item.revision, adopt=True, content=content
            )
        elif action == "all":
            result = [
                decide_from_view(memory, access, r.id, r.revision, adopt=True)
                for r in rows
                if r.status == "ready"
            ]
        else:
            result = decide_from_view(
                memory, access, identifier, item.revision, adopt=action == "adopt"
            )
        emit(args, redactor, result)
        rows = memory.candidates(access)


def decide_from_view(memory, access, identifier, revision, **kwargs):
    store = SQLiteStore(memory.store.path, memory.store.redactor)
    try:
        return SQLiteMemory(store).decide(access, identifier, revision, **kwargs)
    finally:
        store.close()


async def interactive_command(text, controller, args):
    root = argparse.ArgumentParser(prog="/memory", exit_on_error=False)
    add_parser(root.add_subparsers(dest="command"))
    try:
        tokens = shlex.split(text[len("/memory") :], posix=False)
        tokens = [
            token[1:-1]
            if len(token) >= 2 and token[0] == token[-1] and token[0] in {'"', "'"}
            else token
            for token in tokens
        ]
        parsed = root.parse_args(["memory", *tokens])
    except (SystemExit, argparse.ArgumentError, ValueError):
        print("/memory [add|search|show|edit|candidates|disable|enable|forget|used|process]")
        return
    parsed.json, parsed.no_color, parsed.plain = False, args.no_color or args.plain, args.plain
    if parsed.memory_operation == "used" and controller.session["id"] is None:
        print("当前草稿没有运行记录。")
        return
    await command(
        parsed,
        controller.home,
        scope_id=controller.session["id"] if controller.lock else None,
        cwd=controller.session["cwd"],
        interactive_override=True,
    )
