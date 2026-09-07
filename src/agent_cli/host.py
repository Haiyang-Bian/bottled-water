"""Compose the same Kernel and execution loop used by the Web host."""

import asyncio
import logging
import signal
import sys
import time
from dataclasses import asdict
from logging.handlers import RotatingFileHandler

from agent_contracts.context import ContextBudget
from agent_subsystems.context.continuation import JournalContinuationReader
from agent_contracts.version import system_version
from .diagnostics import effective_limits
from agent_contracts.execution import ResourceGrant, WorkspaceSpec
from agent_contracts.errors import ConfigurationError
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.context.local import LocalContextProvider
from agent_subsystems.workspaces.paths import canonical_directory
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.tools import LocalToolExecutor, TrustAuthorization


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
                f"usage={result.usage.total_tokens} estimated={result.usage.estimated} "
                f"incomplete={result.usage.incomplete} counters={result.counters}\n"
                f"Continue: agenthub --resume {result.context_scope_id}",
                file=sys.stderr,
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
    execution_limits, limits = effective_limits(config, profile)
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
        context_budget=ContextBudget(
            profile.max_context_chars, profile.context_window_tokens, profile.max_tokens
        ),
        execution_limits=execution_limits,
    )
    engine = RuntimeEngine(
        agent_executor=executor,
        context_store=store,
        run_journal=store,
        limits=limits,
        continuation_reader=JournalContinuationReader(store),
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
                    "system_version": system_version(),
                    "model": profile.model,
                    "provider": profile.provider,
                    "profile": config.get(
                        "active_profile", config.get("default_profile", "default")
                    ),
                    "effective_limits": {
                        "execution": asdict(execution_limits),
                        "run": asdict(limits),
                        "max_context_chars": profile.max_context_chars,
                        "context_window_tokens": profile.context_window_tokens,
                    },
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
