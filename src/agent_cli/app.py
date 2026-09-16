"""Interactive CLI orchestration. Browsing never constructs a model client."""

import json
import logging
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.version import system_version
from agent_runtime.core.ports import ContextConflictError
from agent_adapters.storage.session_lock import SessionBusyError
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory
from .config import load_config, select_profile
from .host import configure_logging, ensure_trusted, run_turn
from .selection import choose_session, pager
from .sessions import SessionController, SessionHistoryReader
from .terminal_text import safe_text
from .ui import UserInterface
from .input import create_prompt
from .privacy import display_redactor


HELP = """/resume       从本机环境列表恢复任务；/resume --here 按当前位置筛选
/new          新会话
/history      分页查看历史
/session      会话详情
/tools        选择并查看已保存工具结果
/memory       管理基础记忆；add / candidates / edit / disable / enable / forget / used
/resources    资源目录；add / search / show / verify / index / process
/software     软件目录；discover / add / verify / enable / disable
/resume 查询  搜索旧任务，如 /resume 昨天的实验
/verbose on|off 详细输出开关
/add-dir PATH 添加目录
/permissions  查看执行模式、长期权限与任务范围
/cd [PATH]    查看或切换默认工作位置（不会增加授权）
/help         帮助
/exit         退出
Ctrl+C        取消当前任务"""


class RunServices:
    def __init__(self, args, home):
        self.args, self.home = args, home
        self.provider = self.handler = None
        self.redactor = display_redactor(home)

    def prepare(self):
        if self.provider:
            return
        from agent_adapters.credentials.local import LocalCredentialStore
        from model_provider import create_provider
        from .provider import LocalModelProvider

        self.config = load_config(self.home)
        if self.args.max_turns is not None:
            from agent_contracts.harness import ExecutionLimits
            value = None if self.args.max_turns == "unlimited" else int(self.args.max_turns)
            ExecutionLimits(max_model_turns=value)
            self.config.setdefault("execution", {})["max_model_turns"] = value
        name, self.profile = select_profile(self.config, self.args.profile)
        self.config["active_profile"] = name
        secret = LocalCredentialStore(self.home / "credentials").resolve(self.profile.credential_ref)
        self.redactor = Redactor([*self.redactor.secrets, secret])
        self.provider = LocalModelProvider(
            create_provider({**asdict(self.profile), "api_key": secret}), self.redactor
        )
        self.handler = configure_logging(self.home, self.redactor)

    async def close(self):
        try:
            if self.provider:
                await self.provider.aclose()
        finally:
            if self.handler:
                logging.getLogger().removeHandler(self.handler)
                self.handler.close()


def show_restored(controller, ui):
    session = controller.session
    summary = next((s for s in controller.catalog.list()
                    if s.id == session["id"]), None)
    ui.note("已恢复：" + (summary.label() if summary else session["id"]), "cyan bold")
    ui.note(f"位置：{session['cwd']} · 版本 {session['workspace_version']}", "dim")
    show_permissions(controller, ui)
    for item in controller._validate(session):
        ui.note(f"目录暂不可用：{item['path']} · {item['reason']}", "yellow")
    turns, _ = SessionHistoryReader(controller.home, controller.redactor).page(session["id"])
    remaining, truncated = 12000, False
    for turn in reversed(turns):
        blocks = [(f"{turn.created} · {turn.state} / {turn.reason_code}", "dim", False),
                  ("你", "cyan bold", False), (turn.request, "", False),
                  ("AgentHub" + ("（未完成输出）" if turn.state != "completed" else ""),
                   "cyan bold", False), (turn.output or "未保存答复", "", True)]
        for tool in turn.tools:
            status = "结果未知" if tool.get("success") is None else (
                "成功" if tool["success"] else "失败"
            )
            blocks.append((f"工具 {tool.get('tool', '未知')} · {status}", "dim", False))
        for text, style, markdown in blocks:
            if len(text) > remaining:
                truncated = True
            visible = text[:remaining]
            if visible:
                ui.markdown(visible) if markdown else ui.note(visible, style)
                remaining -= len(visible)
    if truncated:
        print("历史回显已截断；/history 可完整分页查看。")
    print("以上为已保存记录；输入后将开始新的 Run。/history 查看更早记录。")


def show_permissions(controller, ui, *, detailed=False):
    from .permissions import task_view
    view = task_view(controller)
    label = "Windows 受限执行" if view["mode"] == "windows_lpac" else "当前用户执行"
    ui.note(f"{label} · 长期权限修订 {view['policy_revision']} · "
            f"{view['selection']['mode']}", "yellow")
    if detailed:
        ui.note(safe_text(json.dumps(view, default=str, ensure_ascii=False)))


async def browse_history(controller):
    reader = SessionHistoryReader(controller.home, controller.redactor)
    cursor = None
    while True:
        turns, cursor = reader.page(controller.session["id"], before=cursor)
        if not turns:
            print("没有更多历史。")
            return
        if not await pager("\n\n".join(t.text() for t in reversed(turns))):
            return
        from prompt_toolkit import PromptSession
        if (await PromptSession().prompt_async("Enter 查看更早 3 轮，q 返回 > ")).strip() == "q":
            return


async def chat(args, home):
    interactive = bool(sys.stdin.isatty() and sys.stdout.isatty() and not args.json)
    if args.resume == "" and not interactive:
        raise ConfigurationError(
            "会话选择需要交互终端。请用 agenthub --json sessions 查询，再用 --resume ID。"
        )
    if args.prompt is None and not interactive:
        raise ConfigurationError("交互模式需要终端；批处理请使用 -p。")
    root = canonical_directory(".")
    services = RunServices(args, home)
    controller = SessionController(
        home, root, lambda store, path: ensure_trusted(store, path, interactive), services.redactor
    )
    ui = UserInterface(args)
    permission_host = None
    execution_options = {name: getattr(args, name, None) for name in ("sandbox", "permissions")}
    execution_options.update({name: getattr(args, name, []) for name in ("read_dir", "write_dir")})

    async def execute(prompt):
        nonlocal permission_host
        with controller.execution() as session:
            execution = None
            if session["execution_mode"] == "windows_lpac":
                from .permission_host import PermissionHost
                from .restricted_assembly import assemble
                if permission_host is None:
                    permission_host = PermissionHost(controller.store)
                    permission_host.start()
                execution = assemble(controller, permission_host, json_mode=args.json)
            services.prepare()
            controller.redactor = controller.catalog.redactor = services.redactor
            controller.store.redactor = services.redactor
            if interactive:
                ui.note("你", "cyan bold")
                ui.note(services.redactor.text(prompt))
                ui.note("AgentHub", "cyan bold")
            if permission_host:
                permission_host.running = True
                permission_host.active_preparation = (
                    execution.driver.prepared.generation if execution else None
                )
            try:
                return await run_turn(
                    controller.store, session, services.provider, services.profile, services.config,
                    services.redactor, prompt, json_mode=args.json,
                    interactive=interactive, plain=args.plain, no_color=not ui.color,
                    verbose=args.verbose, execution=execution,
                )
            finally:
                try:
                    if execution:
                        await execution.driver.aclose()
                finally:
                    if permission_host:
                        permission_host.running = False
                        permission_host.active_preparation = None

    def prompt_for_session():
        return create_prompt(home, controller.session["id"], color=ui.color,
                             cwd=controller.session["cwd"])

    try:
        identifier = args.resume
        filter_root = root if args.here else None
        if args.continue_session:
            candidates = controller.catalog.list(filter_root)
            if not candidates:
                raise ConfigurationError("所选范围没有执行过任务的会话。运行 agenthub 开始新任务。")
            identifier = candidates[0].id
        elif identifier == "":
            identifier = await choose_session(controller.catalog, filter_root, color=ui.color,
                query=getattr(args, "query", ""), since=getattr(args, "since", None),
                until=getattr(args, "until", None))
            if identifier is None:
                return 0
        if identifier:
            await controller.activate(identifier, args.add_dir, args.cwd,
                                      execution_options=execution_options)
            if interactive and args.prompt is None:
                show_restored(controller, ui)
        else:
            controller.default_mode()
            controller.configure(args.add_dir, args.cwd, startup=True,
                                 execution_options=execution_options)
        if args.prompt is not None:
            return await execute(args.prompt)

        prompt_session = prompt_for_session()
        ui.note(f"AgentHub {system_version()} · {controller.session['cwd']}", "cyan bold")
        show_permissions(controller, ui)
        try:
            config = load_config(home)
            name, profile = select_profile(config, args.profile)
            ui.note(f"{name} · {profile.provider} / {profile.model}", "dim")
        except ConfigurationError:
            ui.note("模型尚未配置；agenthub init 可初始化。浏览历史不需要模型连接。", "yellow")
        if not identifier:
            print("新会话 · 提交任务后保存")
            if controller.catalog.list():
                ui.note("本机环境有历史任务。/resume 从列表恢复；agenthub -c 快速续聊。", "cyan")
        print("/help 查看命令")
        while True:
            try:
                prompt = (await prompt_session.prompt_async([("class:prompt", "agenthub> ")])).strip()
                if not prompt:
                    continue
                if prompt in {"/exit", "/quit"}:
                    return 0
                if prompt == "/help":
                    print(HELP)
                elif prompt == "/new":
                    controller.new()
                    prompt_session = prompt_for_session()
                    print("新会话 · 提交任务后保存；/resume 恢复已有会话")
                elif prompt == "/resume" or prompt.startswith("/resume "):
                    query = prompt[len("/resume"):].strip()
                    here = query == "--here" or query.startswith("--here ")
                    if here:
                        query = query[len("--here"):].strip()
                    target = await choose_session(
                        controller.catalog,
                        Path(controller.session["cwd"]) if here else None,
                        controller.session["id"], color=ui.color, query=query
                    )
                    if target:
                        await controller.activate(target)
                        prompt_session = prompt_for_session()
                        show_restored(controller, ui)
                elif prompt == "/history":
                    await browse_history(controller)
                elif any(prompt == name or prompt.startswith(name + " ")
                         for name in ("/resources", "/software")):
                    from .resources import interactive_command
                    await interactive_command(prompt, controller, args)
                elif prompt == "/memory" or prompt.startswith("/memory "):
                    from .memory import interactive_command
                    await interactive_command(prompt, controller, args)
                elif prompt == "/tools":
                    from .tool_details import browse_tools
                    await browse_tools(controller, color=ui.color)
                elif prompt in {"/verbose on", "/verbose off"}:
                    args.verbose = prompt.endswith(" on")
                    ui.note("详细输出已开启" if args.verbose else "详细输出已关闭", "dim")
                elif prompt == "/session":
                    print(safe_text(controller.redactor.dumps(controller.session)))
                elif prompt == "/permissions":
                    show_permissions(controller, ui, detailed=True)
                elif prompt.startswith("/add-dir "):
                    value = prompt[len("/add-dir "):]
                    access = "read"
                    if " --access " in value:
                        value, access = value.rsplit(" --access ", 1)
                        if access not in {"read", "modify"}:
                            raise ConfigurationError("--access 只接受 read 或 modify。")
                    controller.configure([path_argument(value)], access=access)
                    ui.note("已更新任务的显式目录授权。", "cyan")
                elif prompt == "/cd" or prompt.startswith("/cd "):
                    if prompt != "/cd":
                        controller.configure(cwd=path_argument(prompt[len("/cd "):]))
                        prompt_session = prompt_for_session()
                    ui.note(f"位置：{controller.session['cwd']} · "
                            f"版本 {controller.session['workspace_version']}", "cyan")
                elif prompt.startswith("/"):
                    print("未知命令。输入 /help 查看可用命令。")
                else:
                    await execute(prompt)
            except EOFError:
                return 0
            except KeyboardInterrupt:
                continue
            except (ConfigurationError, OperationError, SessionBusyError, ContextConflictError,
                    sqlite3.Error, OSError, ValueError) as exc:
                print(safe_text(services.redactor.text(str(exc))), file=sys.stderr)
    finally:
        try:
            if permission_host:
                await permission_host.close()
        finally:
            controller.close()
            await services.close()


def list_sessions(args, home):
    from .sessions import SessionCatalogReader
    rows = SessionCatalogReader(home, display_redactor(home)).list(
        canonical_directory(".") if args.here else None
    )
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False))
    else:
        ui = UserInterface(args)
        if ui.rich:
            from rich.table import Table
            from rich.text import Text
            table = Table(title="会话 · agenthub -r 从列表恢复", expand=True)
            for column in ("任务", "最近执行", "轮数", "状态"):
                table.add_column(column)
            table.add_column("保存位置")
            for row in rows:
                values = [Text(safe_text(row.title)), Text(row.last_active[:16]),
                          Text(str(row.run_count)), Text(row.state)]
                values.append(Text(safe_text(row.cwd)))
                table.add_row(*values)
            ui.console.print(table)
            if not rows:
                ui.note("没有执行过任务的会话。", "dim")
            return 0
        print("会话 · 使用 agenthub -r 从列表恢复")
        for index, row in enumerate(rows, 1):
            print(safe_text(f"{index}. {row.label()}\n   {row.preview}"))
            print(safe_text(f"   保存位置：{row.cwd} · agenthub --resume {row.id}"))
        if not rows:
            print("没有执行过任务的会话。")
    return 0


def path_argument(value):
    value = value.strip()
    if value.startswith(('"', "'")):
        if len(value) < 2 or value[-1] != value[0]:
            raise ConfigurationError("路径引号未闭合。")
        value = value[1:-1]
    if not value:
        raise ConfigurationError("请提供目录路径。")
    return value


async def history_command(args, home):
    reader = SessionHistoryReader(home, display_redactor(home))
    identifier = args.history_id
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.json
    if not identifier:
        if not interactive:
            raise ConfigurationError("历史选择需要终端；请先用 agenthub --json sessions 查询 ID。")
        identifier = await choose_session(reader, canonical_directory(".") if args.here else None,
                                          color=UserInterface(args).color)
        if identifier is None:
            return 0
    reader.resolve(identifier)
    if interactive:
        await browse_history(SimpleNamespace(home=home, session={"id": identifier},
                                             redactor=reader.redactor))
        return 0
    cursor = None
    while True:
        turns, cursor = reader.page(identifier, before=cursor)
        if not turns:
            break
        for turn in reversed(turns):
            if args.json:
                print(reader.redactor.dumps({"type": "history.turn", **asdict(turn)}))
            else:
                print(safe_text(turn.text()))
    return 0
