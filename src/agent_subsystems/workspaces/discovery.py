"""Discovery policy; authorization and filesystem access remain in the local adapter."""

from pathlib import Path

from pathspec import GitIgnoreSpec


class DiscoveryPolicy:
    generated = {".git", ".venv", "node_modules", "__pycache__", ".next", "target"}

    def __init__(self, root, *, include_ignored=False, tracked=(), index_state="unavailable"):
        self.root = root
        self.include_ignored = include_ignored
        self.tracked = {Path(p) for p in tracked}
        self.tracked_parents = {parent for path in self.tracked for parent in path.parents}
        self.index_state = index_state
        self.rules = []
        self.ignored_directories = 0
        self.warnings = []

    def add_rules(self, directory, text):
        self.rules.append((directory, GitIgnoreSpec.from_lines(text.splitlines())))

    def visible(self, path, *, directory=False):
        if self.include_ignored or path in self.tracked:
            return True
        if directory and path in self.tracked_parents:
            return True
        relative = path.relative_to(self.root)
        ignored = any(part in self.generated for part in relative.parts)
        for base, spec in self.rules:
            if not path.is_relative_to(base):
                continue
            name = path.relative_to(base).as_posix() + ("/" if directory else "")
            match = spec.check_file(name).include
            if match is not None:
                ignored = match
        if directory and ignored:
            self.ignored_directories += 1
        return not ignored

    def diagnostics(self, recursive):
        return {
            "path": str(self.root),
            "recursive": recursive,
            "include_ignored": self.include_ignored,
            "index_state": self.index_state,
            "discovery_degraded": self.index_state in {"unavailable", "truncated", "failed"},
            "ignored_directories": self.ignored_directories,
            "warnings": self.warnings,
        }
