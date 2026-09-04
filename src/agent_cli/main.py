"""Installable CLI entry point with lazy optional dependency imports."""

import argparse
import asyncio
import getpass
import json
import sys
from dataclasses import asdict

from agent_contracts.errors import ConfigurationError, OperationError
from .config import Profile, home_directory, load_config, save_config, select_profile


def parser():
    root = argparse.ArgumentParser(prog="agenthub", description="AgentHub local coding agent")
    root.add_argument("--version", action="version", version="agenthub 0.1.0")
    root.add_argument("-p", "--prompt")
    session = root.add_mutually_exclusive_group()
    session.add_argument("--continue", dest="continue_session", action="store_true")
    session.add_argument("--resume")
    root.add_argument("--add-dir", action="append", default=[])
    root.add_argument("--profile")
    root.add_argument("--json", action="store_true")
    commands = root.add_subparsers(dest="command")
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
    sessions.add_argument("--all", action="store_true")
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
    config.setdefault("profiles", {})[args.profile] = asdict(profile)
    config["default_profile"] = args.profile
    save_config(home, config)
    for name in ("credentials", "locks", "logs", "tmp"):
        (home / name).mkdir(parents=True, exist_ok=True)
    print(f"Configured {args.profile}: {home / 'config.toml'}", file=sys.stderr)
    return 0


async def dispatch(args):
    home = home_directory()
    if args.command == "init":
        return initialize(args, home)
    if args.command == "config":
        from agent_runtime.runtime.run_journal import _sanitize_value

        print(json.dumps(_sanitize_value(load_config(home)), ensure_ascii=False, indent=2))
        return 0
    from agent_adapters.storage.sqlite import SQLiteStore
    from agent_subsystems.workspaces.paths import canonical_directory

    store = SQLiteStore(home / "state.sqlite3")
    try:
        if args.command == "trust":
            path = canonical_directory(args.path)
            store.trust(path, args.operation == "add")
            print(f"Trust {args.operation}: {path}", file=sys.stderr)
            return 0
        if args.command == "sessions":
            print(
                json.dumps(
                    store.sessions(None if args.all else canonical_directory(".")),
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
        if args.command == "replay":
            cursor = 0
            while True:
                page = await store.read_events(args.run_id, after_sequence=cursor)
                for event in page.items:
                    print(store.redactor.dumps(event))
                if not page.items:
                    break
                cursor = page.next_sequence
            return 0
        from agent_adapters.credentials.local import LocalCredentialStore
        from agent_adapters.local.processes import executable, powershell_executable

        config = load_config(home)
        _, profile = select_profile(config, args.profile)
        secret = LocalCredentialStore(home / "credentials").resolve(profile.credential_ref)
        if args.command == "doctor":
            print(
                json.dumps(
                    {
                        "home": str(home),
                        "provider": profile.provider,
                        "model": profile.model,
                        "credential": "available",
                        "powershell": powershell_executable(config.get("powershell")),
                        "git": executable("git"),
                        "execution_mode": "current_user",
                        "filesystem_isolation": False,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 0
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
        from .host import chat

        return await chat(args, home, config, profile, store, raw, secret)
    finally:
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
