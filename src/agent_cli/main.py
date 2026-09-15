"""Installable CLI entry point with lazy optional dependency imports."""

import argparse
import asyncio
import getpass
import json
import sys
from dataclasses import asdict
from agent_contracts.version import system_version

from agent_contracts.errors import ConfigurationError, OperationError
from .config import Profile, home_directory, load_config, save_config, select_profile


def parser():
    root = argparse.ArgumentParser(prog="agenthub", description="AgentHub local coding agent")
    root.add_argument("--version", action="version", version=f"agenthub {system_version()}")
    root.add_argument("-p", "--prompt")
    session = root.add_mutually_exclusive_group()
    session.add_argument("-c", "--continue", dest="continue_session", action="store_true")
    session.add_argument("-r", "--resume", nargs="?", const="", default=None)
    root.add_argument("--add-dir", action="append", default=[])
    root.add_argument("--here", action="store_true", help="Filter tasks by saved working location")
    root.add_argument("--cwd", help="Explicit working location within the task's granted roots")
    root.add_argument("--profile")
    root.add_argument("--max-turns", help="Model request limit: positive integer or unlimited")
    root.add_argument("--json", action="store_true")
    root.add_argument("--plain", action="store_true", help="Plain text without animation")
    root.add_argument("--no-color", action="store_true")
    root.add_argument("--verbose", action="store_true", help="Detailed tool and phase output")
    commands = root.add_subparsers(dest="command")
    from .memory import add_parser as add_memory_parser
    add_memory_parser(commands)
    from .resources import add_parser as add_resources_parser
    add_resources_parser(commands)
    resume = commands.add_parser("resume", help="Choose a saved task in this local environment")
    resume.add_argument("resume_id", nargs="?", default="")
    resume.add_argument("--query", default="")
    resume.add_argument("--since")
    resume.add_argument("--until")
    resume.add_argument("--here", action="store_true", default=argparse.SUPPRESS)
    resume.add_argument("--cwd", default=argparse.SUPPRESS)
    history = commands.add_parser("history", help="Read saved task history without executing")
    history.add_argument("history_id", nargs="?", default="")
    history.add_argument("--here", action="store_true", default=argparse.SUPPRESS)
    state = commands.add_parser("state").add_subparsers(dest="operation", required=True)
    state.add_parser("upgrade", help="Back up and upgrade local state")
    init = commands.add_parser("init", help="Configure a model and credential reference")
    init.add_argument("--provider", choices=["openai_compatible", "deepseek"])
    init.add_argument("--model")
    init.add_argument("--base-url")
    init.add_argument("--credential-env")
    init.add_argument("--profile", default="default")
    cfg = commands.add_parser("config").add_subparsers(dest="operation", required=True)
    cfg.add_parser("show")
    commands.add_parser("doctor", help="Check local capabilities without a model request")
    trust = commands.add_parser("trust").add_subparsers(dest="operation", required=True)
    for operation in ("add", "remove"):
        trust.add_parser(operation).add_argument("path")
    sessions = commands.add_parser("sessions")
    scope = sessions.add_mutually_exclusive_group()
    scope.add_argument("--all", action="store_true")
    scope.add_argument("--here", action="store_true", default=argparse.SUPPRESS)
    commands.add_parser("replay").add_argument("run_id")
    model = commands.add_parser("model").add_subparsers(dest="operation", required=True)
    model.add_parser("check", help="Explicitly send a short model request").add_argument(
        "--profile"
    )
    return root


def initialize(args, home):
    from agent_adapters.credentials.local import LocalCredentialStore
    from .host import ask

    interactive = sys.stdin.isatty()
    if not interactive and (not args.provider or not args.model or not args.credential_env):
        raise ConfigurationError(
            "Non-interactive init requires --provider, --model and --credential-env"
        )
    provider = args.provider or ask("Provider [openai_compatible/deepseek]: ")
    model = args.model or ask("Model ID: ")
    base_url = args.base_url or (
        "https://api.deepseek.com"
        if provider == "deepseek"
        else (ask("Base URL: ") if interactive else "")
    )
    credentials = LocalCredentialStore(home / "credentials")
    reference = (
        "env:" + args.credential_env
        if args.credential_env
        else credentials.save(getpass.getpass("API key (hidden): ", stream=sys.stderr))
    )
    profile = Profile(provider, model, reference, base_url)
    config = load_config(home) if (home / "config.toml").exists() else {}
    config.setdefault("profiles", {})[args.profile] = {
        key: value for key, value in asdict(profile).items() if value is not None
    }
    config["default_profile"] = args.profile
    save_config(home, config)
    for name in ("credentials", "locks", "logs", "tmp"):
        (home / name).mkdir(parents=True, exist_ok=True)
    print(f"Configured {args.profile}: {home / 'config.toml'}", file=sys.stderr)
    return 0


async def dispatch(args):
    home = home_directory()
    if args.command in {"resources", "software"}:
        from .resources import command
        return await command(args, home)
    if args.command == "memory":
        from .memory import command
        return await command(args, home)
    if args.command == "resume" and args.resume_id and (args.query or args.since or args.until):
        raise ConfigurationError("查询/日期筛选不能与显式任务 ID 同用。")
    identifier = args.resume or getattr(args, "resume_id", "") or getattr(args, "history_id", "")
    if args.here and identifier:
        raise ConfigurationError("--here 不能与显式任务 ID 同用。")
    if args.here and not (args.continue_session or args.resume is not None
                         or args.command in {"resume", "sessions", "history"}):
        raise ConfigurationError("--here 用于 -c、-r、resume、sessions 或 history。")
    if args.here and getattr(args, "all", False):
        raise ConfigurationError("--here 不能与 --all 同用。")
    if (args.cwd or args.add_dir) and args.command not in {None, "resume"}:
        raise ConfigurationError("--cwd 和 --add-dir 只用于新建或恢复任务。")
    if args.command == "init":
        from agent_adapters.storage.sqlite import SQLiteStore
        store = SQLiteStore(home / "state.sqlite3")
        store.close()
        return initialize(args, home)
    if args.command == "config":
        from agent_runtime.runtime.run_journal import _sanitize_value

        print(json.dumps(_sanitize_value(load_config(home)), ensure_ascii=False, indent=2))
        return 0
    if args.command == "resume":
        if args.continue_session or args.resume is not None:
            raise ConfigurationError("resume 子命令不能与 -c/--resume 同时使用。")
        args.resume = args.resume_id
    if args.command in (None, "resume"):
        from .app import chat
        return await chat(args, home)
    if args.command == "sessions":
        from .app import list_sessions
        return list_sessions(args, home)
    if args.command == "history":
        from .app import history_command
        return await history_command(args, home)
    from agent_adapters.storage.sqlite import SQLiteStore
    from agent_subsystems.workspaces.paths import canonical_directory

    from .privacy import display_redactor
    readonly = args.command in {"replay", "doctor", "model"}
    path = home / "state.sqlite3"
    store = SQLiteStore(path, readonly=readonly) if path.exists() or not readonly else None
    if store:
        store.redactor = display_redactor(home)
    try:
        if args.command == "state":
            print(json.dumps({"database_version": store.schema_version,
                              "environment_id": store.environment.environment_id,
                              "backup": str(store.backup_path) if store.backup_path else None}))
            return 0
        if args.command == "trust":
            path = canonical_directory(args.path)
            store.trust(path, args.operation == "add")
            print(f"Trust {args.operation}: {path}", file=sys.stderr)
            return 0
        if args.command == "replay":
            if store is None or store.queries().run_scope(args.run_id) is None:
                raise ConfigurationError("Run 不存在或不属于当前本机环境。")
            cursor = 0
            while True:
                page = await store.read_events(args.run_id, after_sequence=cursor)
                for event in page.items:
                    displayed = asdict(event)
                    if event.type in {
                        "system.run_started",
                        "system.run_completed",
                        "system.run_failed",
                        "system.run_cancelled",
                    }:
                        for field in ("system_version", "effective_limits", "counters", "usage"):
                            displayed["payload"].setdefault(field, None)
                    print(store.redactor.dumps(displayed))
                if not page.items:
                    break
                cursor = page.next_sequence
            return 0
        from agent_adapters.credentials.local import LocalCredentialStore

        if args.command == "doctor":
            from .diagnostics import doctor

            try:
                config = load_config(home)
            except ConfigurationError:
                config = {}
            report = doctor(home, store, config, args.profile)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report["healthy"] else 2
        config = load_config(home)
        if args.max_turns is not None:
            value = None if args.max_turns == "unlimited" else int(args.max_turns)
            from agent_contracts.harness import ExecutionLimits

            ExecutionLimits(max_model_turns=value)
            config.setdefault("execution", {})["max_model_turns"] = value
        name, profile = select_profile(config, args.profile)
        config["active_profile"] = name
        secret = LocalCredentialStore(home / "credentials").resolve(profile.credential_ref)
        from model_provider import create_provider
        from agent_subsystems.observability.redaction import Redactor
        from .provider import LocalModelProvider

        raw = create_provider({**asdict(profile), "api_key": secret})
        if args.command == "model":
            from model_provider.core.interfaces import ChatMessage

            provider = LocalModelProvider(raw, Redactor([secret]))
            try:
                response = await provider.chat(
                    [ChatMessage("user", "Reply with OK.")], max_tokens=16
                )
                print(
                    provider.redactor.dumps(
                        {
                            "provider": profile.provider,
                            "model": profile.model,
                            "output": response.content,
                            "usage": response.usage,
                        }
                    )
                )
                return 0
            finally:
                await provider.aclose()
    finally:
        if store:
            store.close()


def main():
    args = parser().parse_args()
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    try:
        return asyncio.run(dispatch(args))
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        from agent_adapters.storage.session_lock import SessionBusyError

        if isinstance(exc, SessionBusyError):
            code, message = 3, str(exc)
        elif isinstance(
            exc, (ConfigurationError, OperationError, OSError, ValueError, KeyError, ImportError)
        ):
            code, message = 2, str(exc)
        else:
            code, message = 1, f"Operation failed ({type(exc).__name__}); see local diagnostics"
        if args.json:
            print(
                json.dumps(
                    {"type": "error", "exit_code": code, "message": message}, ensure_ascii=False
                )
            )
        else:
            print(message, file=sys.stderr)
        return code


if __name__ == "__main__":
    raise SystemExit(main())
