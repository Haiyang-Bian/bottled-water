"""Fresh, repository-owned restricted Runtime fixture; never a production installer."""

from contextlib import ExitStack, contextmanager
import builtins
import io
import os
from pathlib import Path
import shutil
import sys
from unittest.mock import patch


def bundle(repo, target):
    import pathspec

    python = target / "python"
    python.mkdir(parents=True)
    base = Path(sys.base_prefix)
    for pattern in ("*.exe", "*.dll"):
        for source in base.glob(pattern):
            shutil.copyfile(source, python / source.name)
    for directory in ("Lib", "DLLs"):
        shutil.copytree(base / directory, python / directory,
                        ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"))
    modules = target / "modules"
    for name in ("agent_contracts", "agent_runtime", "agent_subsystems", "agent_adapters"):
        shutil.copytree(repo / "src" / name, modules / name,
                        ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(Path(pathspec.__file__).parent, modules / "pathspec",
                    ignore=shutil.ignore_patterns("__pycache__"))
    (target / "worker.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0,str(Path(__file__).parent/'modules'))\n"
        "from agent_adapters.local.restricted_worker import main\nmain()\n", encoding="utf-8")
    return {"python": str(python / "python.exe")}


@contextmanager
def block_host_business_io(root, enabled, violations):
    """Catch host file probes after trusted preparation, including normal discovery."""
    prefix = os.path.normcase(str(root))

    def guarded(original):
        def call(path, *args, **kwargs):
            if enabled[0] and isinstance(path, (str, bytes, os.PathLike)):
                value = os.path.normcase(os.fsdecode(path))
                if value == prefix or value.startswith(prefix + os.sep):
                    violations.append({"operation": original.__name__, "path": value})
                    raise AssertionError("Host business filesystem bypass: " + original.__name__)
            return original(path, *args, **kwargs)
        return call

    with ExitStack() as stack:
        for obj, name in ((builtins, "open"), (io, "open"), (os, "stat"), (os, "lstat"),
                          (os, "listdir"), (os, "scandir")):
            stack.enter_context(patch.object(obj, name, guarded(getattr(obj, name))))
        yield
