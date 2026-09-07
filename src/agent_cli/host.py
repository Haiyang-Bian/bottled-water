"""Compose the same Kernel and execution loop used by the Web host."""

import asyncio
import logging
import signal
import sys
import time
from dataclasses import asdict
from logging.handlers import RotatingFileHandler

from agent_contracts.context import ContextBudget
from agent_contracts.harness import ExecutionLimits
from agent_contracts.execution import ResourceGrant, WorkspaceSpec
from agent_contracts.errors import ConfigurationError, OperationError
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine, RuntimeLimits
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.context.local import LocalContextProvider
from agent_subsystems.observability.redaction import Redactor
from agent_subsystems.workspaces.paths import canonical_directory
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.tools import LocalToolExecutor, TrustAuthorization
from agent_adapters.storage.session_lock import SessionLock
from .provider import LocalModelProvider


class Renderer:
    def __init__(self, redactor, *, json_mode=False, interactive=False):
        self.redactor, self.json_mode, self.interactive = redactor, json_mode, interactive
        self.wrote_tokens = False

    def event(self, event):
        if event.type in {"agent.thinking", "model.reasoning", "model.thinking"}:
            return
        if self.json_mode:
            print(self.redactor.dumps(event), flush=True)
        elif event.type == "agent.token" and self.interactive:
            print(self.redactor.text(event.payload.get("token", "")), end="", flush=True)
            self.wrote_tokens = True
        elif event.type in {"execution.phase_started", "execution.phase_finished"}:
            p = event.payload
            elapsed = f" {p['elapsed_seconds']:.2f}s" if "elapsed_seconds" in p else ""
            print(f"[{p['phase']}] {event.type.rsplit('_', 1)[-1]}{elapsed}", file=sys.stderr)
        elif event.type in {"agent.tool_call", "agent.tool_result"}:
            payload = event.payload
            tool = payload.get("tool") or ", ".join(payload.get("tools", []))
            status = (
                "start"
                if event.type == "agent.tool_call"
                else ("ok" if payload.get("success") else "failed")
            )
            print(self.redactor.text(f"\n[{tool}] {status}"), file=sys.stderr, flush=True)
            if event.type == "agent.tool_result":
                result = payload.get("result")
                if isinstance(result, dict):
                    for key in ("stdout", "stderr"):
                        if result.get(key):
                            print(self.redactor.text(result[key]), file=sys.stderr)
                if payload.get("error"):
                    print(self.redactor.text(str(payload["error"])), file=sys.stderr)

    def result(self, result):
        if self.json_mode:
            print(self.redactor.dumps({"type": "result", **asdict(result)}), flush=True)
        else:
            print("" if self.wrote_tokens else self.redactor.text(result.output))
            print(
                f"[{result.state.value}: {result.reason_code}] run={result.run_id} "
                f"usage={result.usage.total_tokens} counters={result.counters}", file=sys.stderr
            )


def configure_logging(home, redactor):
    directory = home / "logs"
    directory.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        directory / "agenthub.log", maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )

    class Formatter(logging.Formatter):
        def format(self, record):
            if not hasattr(record, "context"):
                record.context = ""
            return redactor.text(super().format(record))

    handler.setFormatter(Formatter("%(asctime)s %(levelname)s %(name)s %(message)s %(context)s"))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    return handler


def ask(prompt):
    print(prompt, end="", file=sys.stderr, flush=True)
    value = sys.stdin.readline()
    if not value:
        raise ConfigurationError("Input closed")
    return value.strip()


def ensure_trusted(store, path, interactive):
    if store.is_trusted(path):
        return
    if not interactive:
        raise ConfigurationError(f'Directory is not trusted. Run: agenthub trust add "{path}"')
    response = ask(
        f"\n信任此目录：{path}\n智能体将自动读写文件，并以当前 Windows 用户权限执行 PowerShell/Git。"
        "这不是系统沙箱；脚本可能访问其他目录和网络。信任会被保存。\n允许？[y/N] "
    )
    if response.lower() not in {"y", "yes", "是", "允许"}:
        raise ConfigurationError("Directory trust declined")
    store.trust(path)


async def run_turn(
    store,
    session,
    provider,
    profile,
    config,
    redactor,
    prompt,
    *,
    json_mode=False,
    interactive=False,
):
    workspace = WorkspaceSpec(
        canonical_directory(session["root"]), tuple(canonical_directory(p) for p in session["dirs"])
    )
    limits = RuntimeLimits(**config.get("limits", {}))
    driver = LocalProcessDriver(redactor)
    renderer = Renderer(redactor, json_mode=json_mode, interactive=interactive)
    grant = ResourceGrant(workspace, frozenset({"files", "process"}))
    executor = AgentLoopExecutor(
        model_provider=provider,
        tool_executor=LocalToolExecutor(
            grant, TrustAuthorization(store), driver, redactor, shell=config.get("powershell")
        ),
        context_provider=LocalContextProvider(workspace, profile.max_history_chars),
        use_streaming=True,
        run_journal=store,
        context_budget=ContextBudget(profile.max_context_chars, profile.context_window_tokens,
                                     profile.max_tokens),
        execution_limits=ExecutionLimits(request_timeout_seconds=profile.timeout_seconds,
                                         **config.get("execution", {})),
    )
    engine = RuntimeEngine(
        agent_executor=executor, context_store=store, run_journal=store, limits=limits
    )
    previous = signal.getsignal(signal.SIGINT)
    try:
        handle = await engine.start(
            RunRequest(
                context_scope_id=session["id"],
                input=prompt,
                agents=(AgentConfig("local", "AgentHub", "You are a local coding assistant."),),
                policy=SingleAgentPolicy(),
                metadata={
                    "session_id": session["id"],
                    "execution_deadline": time.monotonic() + limits.wall_time_seconds,
                },
            )
        )
        loop = asyncio.get_running_loop()

        def interrupt(signum, frame):
            loop.call_soon_threadsafe(lambda: asyncio.create_task(handle.cancel("user_cancelled")))

        signal.signal(signal.SIGINT, interrupt)
        async for event in handle.events():
            renderer.event(event)
        result = await handle.result()
        renderer.result(result)
        return {"completed": 0, "failed": 1, "cancelled": 130}[result.state.value]
    finally:
        signal.signal(signal.SIGINT, previous)
        await engine.shutdown()
        await driver.aclose()


async def chat(args, home, config, profile, store, raw_provider, secret):
    redactor = Redactor([secret])
    store.redactor = redactor
    provider = LocalModelProvider(raw_provider, redactor)
    log_handler = configure_logging(home, redactor)
    interactive = bool(sys.stdin.isatty() and not args.json)
    root = canonical_directory(".")
    try:
        ensure_trusted(store, root, interactive)
        if args.resume:
            session = store.session(args.resume)
            if session is None or session["root"] != str(root):
                raise ConfigurationError("Session does not belong to the current directory")
        elif args.continue_session:
            sessions = store.sessions(root)
            if not sessions:
                raise ConfigurationError("No previous session exists in this directory")
            session = sessions[0]
        else:
            session = store.new_session(root)
        with SessionLock(home / "locks", session["id"]):
            await store.recover_session(session["id"])
            for path in [*session["dirs"], *args.add_dir]:
                directory = canonical_directory(path)
                ensure_trusted(store, directory, interactive)
                if str(directory) not in session["dirs"] and directory != root:
                    session["dirs"].append(str(directory))
            store.set_directories(session["id"], session["dirs"])
            if args.prompt is not None:
                return await run_turn(
                    store,
                    session,
                    provider,
                    profile,
                    config,
                    redactor,
                    args.prompt,
                    json_mode=args.json,
                )
            if not interactive:
                raise ConfigurationError(
                    "Interactive mode requires a terminal; use -p for batch tasks"
                )
            from prompt_toolkit import PromptSession
            from prompt_toolkit.patch_stdout import patch_stdout

            prompt_session = PromptSession()
            print(f"AgentHub · {root}\nSession: {session['id']}\n/help 查看命令", file=sys.stderr)
            with patch_stdout():
                while True:
                    try:
                        prompt = (await prompt_session.prompt_async("agenthub> ")).strip()
                    except EOFError:
                        return 0
                    except KeyboardInterrupt:
                        continue
                    if not prompt:
                        continue
                    if prompt in {"/exit", "/quit"}:
                        return 0
                    if prompt == "/help":
                        print(
                            "/add-dir PATH  添加目录\n/session  会话信息\n/exit  退出\nCtrl+C  取消运行"
                        )
                    elif prompt == "/session":
                        print(redactor.dumps(session))
                    elif prompt.startswith("/add-dir "):
                        try:
                            path = canonical_directory(
                                prompt[len("/add-dir ") :].strip().strip('"')
                            )
                            ensure_trusted(store, path, True)
                            if str(path) not in session["dirs"] and path != root:
                                session["dirs"].append(str(path))
                            store.set_directories(session["id"], session["dirs"])
                        except (ConfigurationError, OperationError, OSError, ValueError) as exc:
                            print(str(exc), file=sys.stderr)
                    else:
                        await run_turn(
                            store,
                            session,
                            provider,
                            profile,
                            config,
                            redactor,
                            prompt,
                            interactive=True,
                        )
    finally:
        await provider.aclose()
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()
