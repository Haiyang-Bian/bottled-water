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


HELP = """/resume       从列表恢复会话
/new          新会话
/history      分页查看历史
/session      会话详情
/add-dir PATH 添加目录
/help         帮助
/exit         退出
Ctrl+C        取消当前任务"""


class RunServices:
    def __init__(self, args, home):
        self.args, self.home = args, home
        self.provider = self.handler = None
        self.redactor = Redactor()

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
        self.redactor = Redactor([secret])
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


def show_restored(controller):
    session = controller.session
    summary = next((s for s in controller.catalog.list(controller.root)
                    if s.id == session["id"]), None)
    print(safe_text("已恢复：" + (summary.label() if summary else session["id"])))
    print(safe_text(f"目录：{session['root']}"))
    turns, _ = SessionHistoryReader(controller.home, controller.redactor).page(session["id"])
    content = "\n\n".join(t.text() for t in reversed(turns))
    print(safe_text(content[:12000]))
    if len(content) > 12000:
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
    controller = SessionController(
        home, root, lambda store, path: ensure_trusted(store, path, interactive)
    )
    services = RunServices(args, home)

    async def execute(prompt):
        services.prepare()
        controller.redactor = controller.catalog.redactor = services.redactor
        controller.writable().redactor = services.redactor
        session = controller.materialize()
        return await run_turn(
            controller.store, session, services.provider, services.profile, services.config,
            services.redactor, prompt, json_mode=args.json,
            interactive=interactive and args.prompt is None,
        )

    try:
        identifier = args.resume
        if args.continue_session:
            candidates = controller.catalog.list(root)
            if not candidates:
                raise ConfigurationError("当前目录没有执行过任务的会话。运行 agenthub 开始新会话。")
            identifier = candidates[0].id
        elif identifier == "":
            identifier = await choose_session(controller.catalog, root)
            if identifier is None:
                return 0
        if identifier:
            await controller.activate(identifier, args.add_dir)
            if interactive and args.prompt is None:
                show_restored(controller)
        else:
            controller.session["dirs"] = list(args.add_dir)
        if args.prompt is not None:
            return await execute(args.prompt)

        from prompt_toolkit import PromptSession
        prompt_session = PromptSession()
        print(safe_text(f"AgentHub {system_version()} · {root}"))
        if not identifier:
            print("新会话 · 提交任务后保存")
            if controller.catalog.list(root):
                print("此目录有历史会话。输入 /resume 从列表恢复；下次可用 agenthub -c 快速续聊。")
        print("/help 查看命令")
        while True:
            try:
                prompt = (await prompt_session.prompt_async("agenthub> ")).strip()
                if not prompt:
                    continue
                if prompt in {"/exit", "/quit"}:
                    return 0
                if prompt == "/help":
                    print(HELP)
                elif prompt == "/new":
                    controller.new()
                    prompt_session = PromptSession()
                    print("新会话 · 提交任务后保存；/resume 恢复已有会话")
                elif prompt == "/resume":
                    target = await choose_session(controller.catalog, root, controller.session["id"])
                    if target:
                        await controller.activate(target)
                        prompt_session = PromptSession()
                        show_restored(controller)
                elif prompt == "/history":
                    await browse_history(controller)
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
    rows = SessionCatalogReader(home).list(None if args.all else canonical_directory("."))
    if args.json:
        print(json.dumps([asdict(row) for row in rows], ensure_ascii=False))
    else:
        print("会话 · 使用 agenthub -r 从列表恢复")
        for index, row in enumerate(rows, 1):
            print(safe_text(f"{index}. {row.label()}\n   {row.preview}"))
            if args.all:
                print(safe_text(f"   目录：{row.root} · 切换到该目录后运行 agenthub -r"))
        if not rows:
            print("没有执行过任务的会话。")
    return 0
