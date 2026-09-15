"""Local diagnostics that do not issue model requests or expose credentials."""

import sys
from dataclasses import asdict

from agent_contracts.version import system_version
from agent_contracts.harness import ExecutionLimits
from agent_runtime import RuntimeLimits
from agent_runtime.runtime.run_journal import _sanitize_value
from agent_subsystems.observability.redaction import Redactor


def effective_limits(config, profile):
    try:
        execution = ExecutionLimits(
            **{"request_timeout_seconds": profile.timeout_seconds, **config.get("execution", {})}
        )
        runtime = RuntimeLimits(**config.get("limits", {}))
    except (TypeError, ValueError) as exc:
        from agent_contracts.errors import ConfigurationError

        raise ConfigurationError("Invalid execution or Run limits") from exc
    return execution, runtime


def doctor(home, store, config, profile_name):
    from agent_adapters.credentials.local import LocalCredentialStore
    from agent_adapters.local.processes import executable, powershell_executable
    from .config import select_profile

    result = {
        "version": system_version(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
        "command": sys.argv[0],
        "home": str(home),
        "config_source": str(home / "config.toml"),
        "database_version": store.schema_version if store else None,
        "upgrade_required": bool(store and store.schema_version < 4),
        "environment_id": store.environment.environment_id if store and store.environment else None,
        "identity_binding": "matched" if store and store.environment else "unbound",
        "binding_kind": store.identity.binding_kind if store and store.identity else None,
        "execution_mode": "current_user",
        "filesystem_isolation": False,
        "network_isolation": False,
        "process_tree_management": "job_object" if sys.platform == "win32" else "process_group",
        "healthy": True,
        "errors": [],
    }
    secrets = []
    try:
        name, profile = select_profile(config, profile_name)
        result.update(profile=name, provider=profile.provider, model=profile.model)
        execution, runtime = effective_limits(config, profile)
        result["effective_limits"] = {
            "execution": asdict(execution),
            "run": asdict(runtime),
            "max_context_chars": profile.max_context_chars,
            "context_window_tokens": profile.context_window_tokens,
        }
        try:
            secrets.append(
                LocalCredentialStore(home / "credentials").resolve(profile.credential_ref)
            )
            result["credential"] = "available"
        except Exception:
            result["credential"] = "unavailable"
            result["errors"].append("Configured credential could not be resolved")
    except Exception:
        result["errors"].append("Missing or invalid model profile/limits")
    for name, resolve in (
        ("powershell", lambda: powershell_executable(config.get("powershell"))),
        ("git", lambda: executable("git")),
    ):
        try:
            result[name] = resolve()
        except Exception:
            result[name] = None
            result["errors"].append(f"{name} unavailable")
    result["effective_config"] = _sanitize_value(config)
    result["healthy"] = not result["errors"]
    return Redactor(secrets).value(result)
