"""Installed shared-system identity without importing optional host dependencies."""

from importlib.metadata import PackageNotFoundError, version


def system_version():
    try:
        return version("agenthub-system")
    except PackageNotFoundError:
        return "unknown (distribution metadata unavailable)"
