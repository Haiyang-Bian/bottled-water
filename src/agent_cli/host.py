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
from agent_contracts.execution import ExecutionLocation, ResourceGrant, WorkspaceSpec
from agent_contracts.errors import ConfigurationError
from agent_runtime import AgentConfig, RunRequest, RuntimeEngine
from agent_subsystems.execution.agent_executor import AgentLoopExecutor
from agent_subsystems.scheduling.single_agent import SingleAgentPolicy
from agent_subsystems.context.local import LocalContextProvider
from agent_subsystems.workspaces.paths import canonical_directory, effective_roots, resolve_resource
from agent_adapters.local.processes import LocalProcessDriver
from agent_adapters.local.tools import LocalToolExecutor, TrustAuthorization
from agent_adapters.storage.memory import SQLiteMemory
from agent_subsystems.memory.tools import MemoryToolExecutor
from agent_subsystems.memory.context import RunMemoryContext
from agent_adapters.storage.resources import SQLiteResources
from agent_adapters.storage.tasks import TaskCatalog
from agent_adapters.local.resources import LocalSoftware, probe
from agent_subsystems.workspaces.resource_tools import ResourceToolExecutor
from agent_subsystems.workspaces.resource_context import RunResourceContext


from .rendering import renderer_for


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
    plain=False,
    no_color=False,
    verbose=False,
):
    roots, inactive = effective_roots(session["granted_roots"], store.is_trusted)
    workspace = WorkspaceSpec(roots)
    location = ExecutionLocation(canonical_directory(session["cwd"]), session["workspace_version"])
    resolve_resource(workspace, location, ".", directory=True)
    execution_limits, limits = effective_limits(config, profile)
    driver = LocalProcessDriver(redactor)
    renderer = renderer_for(redactor, json_mode=json_mode, interactive=interactive,
                            plain=plain, no_color=no_color, verbose=verbose)
    grant = ResourceGrant(workspace, frozenset({"files", "process"}))
    memory = SQLiteMemory(store)
    memory_access = memory.access(scope_id=session["id"])
    resources = SQLiteResources(store)
    resource_access = resources.access(scope_id=session["id"])
    tasks = TaskCatalog(store)
    executor = AgentLoopExecutor(
        model_provider=provider,
        tool_executor=MemoryToolExecutor(ResourceToolExecutor(LocalToolExecutor(
            grant, location, TrustAuthorization(store), driver, redactor, shell=config.get("powershell")
        ), resources, resource_access, LocalSoftware(driver), tasks, probe, redactor), memory, memory_access),
        context_provider=LocalContextProvider(workspace, location, profile.max_history_chars),
        use_streaming=True,
        run_journal=store,
        context_budget=ContextBudget(
            profile.max_context_chars, profile.context_window_tokens, profile.max_tokens
        ),
        execution_limits=execution_limits,
        memory_context=RunMemoryContext(memory, memory_access, prompt, location.cwd),
        resource_context=RunResourceContext(resources, resource_access, prompt, tasks),
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
                agents=(AgentConfig(store.environment.default_agent_id, "AgentHub",
                                    "You are a local coding assistant."),),
                policy=SingleAgentPolicy(),
                metadata={
                    "memory_enabled": True,
                    "resources_enabled": True,
                    "session_id": session["id"],
                    "environment_id": store.environment.environment_id,
                    "agent_id": store.environment.default_agent_id,
                    "execution_location": {"cwd": str(location.cwd), "version": location.version},
                    "effective_roots": [str(p) for p in roots],
                    "inactive_roots": inactive,
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
        try:
            resources.process(resource_access)
        except Exception:
            print("资源索引待处理；agenthub resources process 可重试。原 Run 终态保留。", file=sys.stderr)
        try:
            memory.process(memory_access)
            count = memory.candidate_count(memory_access, result.run_id)
            if count and not json_mode:
                print(f"记忆候选：{count} · /memory candidates 查看并采纳。",
                      file=sys.stderr)
        except Exception:
            print("记忆待处理队列未完成；运行 agenthub memory process 重试。Run 终态未改变。",
                  file=sys.stderr)
        return {"completed": 0, "failed": 1, "cancelled": 130}[result.state.value]
    finally:
        renderer.close()
        signal.signal(signal.SIGINT, previous)
        await engine.shutdown()
        await driver.aclose()
