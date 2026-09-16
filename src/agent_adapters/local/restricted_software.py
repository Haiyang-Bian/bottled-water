"""Registered software mapped by the trusted host to verified isolated copies."""

from pathlib import Path

from agent_contracts.errors import OperationError
from .file_operations import BoundFileOperations


class RestrictedSoftware:
    def __init__(self, driver, mapping):
        self.driver, self.mapping = driver, dict(mapping)

    async def run(self, cfg, args, context, *, cwd=".", timeout=120, outputs=()):
        if not cfg.enabled:
            raise OperationError("software_disabled", "Software is disabled")
        kind = self.mapping.get(cfg.resource_id)
        item = self.driver.software.get(kind)
        if item is None or cfg.kind != kind:
            raise OperationError("software_incompatible", "No approved isolated copy for this software ID")
        if cfg.sha256 != item["sha256"]:
            raise OperationError("software_changed", "Registered software differs from isolated copy")
        if len(outputs) > 20 or len(args) > 200 or any("\0" in value for value in args):
            raise OperationError("software_argument_limit", "At most 20 outputs and 200 arguments; no NUL")
        files = BoundFileOperations(self.driver, context)
        directory = await files.resolve(cwd, directory=True)
        paths = [str(directory / path) for path in outputs]
        before = [await files.probe(path) for path in paths]
        argv = [item["path"], *(["--no-pager"] if kind == "git" else []), *args]
        result = await self.driver.run(argv, directory, context=context, timeout=timeout)
        result["outputs"] = []
        for path, previous in zip(paths, before):
            try:
                actual = await files.resolve(path)
                after = await files.probe(actual)
            except OperationError as exc:
                actual, after = Path(path), {"observation_status": "unknown", "error": exc.code}
            result["outputs"].append({"path": str(actual), "before": previous, "after": after})
        result.update(software_id=cfg.resource_id, software_sha256=cfg.sha256,
                      software_version=cfg.version,
                      notice="Output observations do not certify correctness or who created a file")
        return result
