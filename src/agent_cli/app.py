"""Interactive CLI orchestration. Browsing never constructs a model client."""

import json
import logging
import sys
from dataclasses import asdict

from agent_contracts.errors import ConfigurationError, OperationError
from agent_contracts.version import system_version
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


HELP = """/resume       从列表恢复会话
/new          新会话
/history      分页查看历史
/session      会话详情
/tools        选择并查看已保存工具结果
/verbose on|off 详细输出开关
/add-dir PATH 添加目录
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
    summary = next((s for s in controller.catalog.list(controller.root)
                    if s.id == session["id"]), None)
    ui.note("已恢复：" + (summary.label() if summary else session["id"]), "cyan bold")
    ui.note(f"目录：{session['root']}", "dim")
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

    async def execute(prompt):
        services.prepare()
        controller.redactor = controller.catalog.redactor = services.redactor
        controller.writable().redactor = services.redactor
        session = controller.materialize()
        if interactive:
            ui.note("你", "cyan bold")
            ui.note(services.redactor.text(prompt))
            ui.note("AgentHub", "cyan bold")
        return await run_turn(
            controller.store, session, services.provider, services.profile, services.config,
            services.redactor, prompt, json_mode=args.json,
            interactive=interactive, plain=args.plain, no_color=not ui.color, verbose=args.verbose,
        )

    try:
        identifier = args.resume
        if args.continue_session:
            candidates = controller.catalog.list(root)
            if not candidates:
                raise ConfigurationError("当前目录没有执行过任务的会话。运行 agenthub 开始新会话。")
            identifier = candidates[0].id
        elif identifier == "":
            identifier = await choose_session(controller.catalog, root, color=ui.color)
            if identifier is None:
                return 0
        if identifier:
            await controller.activate(identifier, args.add_dir)
            if interactive and args.prompt is None:
                show_restored(controller, ui)
        else:
            controller.session["dirs"] = list(args.add_dir)
        if args.prompt is not None:
            return await execute(args.prompt)

        prompt_session = create_prompt(home, controller.session["id"], color=ui.color)
        ui.note(f"AgentHub {system_version()} · {root}", "cyan bold")
        try:
            config = load_config(home)
            name, profile = select_profile(config, args.profile)
            ui.note(f"{name} · {profile.provider} / {profile.model}", "dim")
        except ConfigurationError:
            ui.note("模型尚未配置；agenthub init 可初始化。浏览历史不需要模型连接。", "yellow")
        if not identifier:
            print("新会话 · 提交任务后保存")
            if controller.catalog.list(root):
                ui.note("此目录有历史会话。/resume 从列表恢复；agenthub -c 快速续聊。", "cyan")
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
                    prompt_session = create_prompt(home, color=ui.color)
                    print("新会话 · 提交任务后保存；/resume 恢复已有会话")
                elif prompt == "/resume":
                    target = await choose_session(
                        controller.catalog, root, controller.session["id"], color=ui.color
                    )
                    if target:
                        await controller.activate(target)
                        prompt_session = create_prompt(home, target, color=ui.color)
                        show_restored(controller, ui)
                elif prompt == "/history":
                    await browse_history(controller)
                elif prompt == "/tools":
                    from .tool_details import browse_tools
                    await browse_tools(controller, color=ui.color)
                elif prompt in {"/verbose on", "/verbose off"}:
                    args.verbose = prompt.endswith(" on")
                    ui.note("详细输出已开启" if args.verbose else "详细输出已关闭", "dim")
                elif prompt == "/session":
                    print(safe_text(controller.redactor.dumps(controller.session)))
                elif prompt.startswith("/add-dir "):
                    path = canonical_directory(prompt[len("/add-dir "):].strip().strip('"'))
                    dirs = controller.directories(controller.session, [path])
                    if controller.session["id"]:
                        controller.store.set_directories(controller.session["id"], dirs)
                    controller.session["dirs"] = dirs
                    print(safe_text(f"已添加目录：{path}"))
                elif prompt.startswith("/"):
                    print("未知命令。输入 /help 查看可用命令。")
                else:
                    await execute(prompt)
            except EOFError:
                return 0
            except KeyboardInterrupt:
                continue
            except (ConfigurationError, OperationError, SessionBusyError, OSError, ValueError) as exc:
                print(safe_text(services.redactor.text(str(exc))), file=sys.stderr)
    finally:
        controller.close()
        await services.close()


def list_sessions(args, home):
    from .sessions import SessionCatalogReader
    rows = SessionCatalogReader(home, display_redactor(home)).list(
        None if args.all else canonical_directory(".")
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
            if args.all:
                table.add_column("目录")
            for row in rows:
                values = [Text(safe_text(row.title)), Text(row.last_active[:16]),
                          Text(str(row.run_count)), Text(row.state)]
                if args.all:
                    values.append(Text(safe_text(row.root)))
                table.add_row(*values)
            ui.console.print(table)
            if not rows:
                ui.note("没有执行过任务的会话。", "dim")
            return 0
        print("会话 · 使用 agenthub -r 从列表恢复")
        for index, row in enumerate(rows, 1):
            print(safe_text(f"{index}. {row.label()}\n   {row.preview}"))
            if args.all:
                print(safe_text(f"   目录：{row.root} · 切换到该目录后运行 agenthub -r"))
        if not rows:
            print("没有执行过任务的会话。")
    return 0
