"""Explicit isolated copies and immutable content manifests; never downloads tools."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from agent_contracts.errors import ConfigurationError
from .dependencies import DependencyManifest


def copy_file(source, destination):
    source = Path(source)
    if source.lstat().st_file_attributes & 0x400:
        raise ConfigurationError("工具副本不接受链接：" + str(source))
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Installed Git uses hard-linked DLLs. Copy bytes to a new, independent file;
    # never carry a source hard link into the isolated dependency tree.
    with source.open("rb") as reader, destination.open("xb") as writer:
        before = os.fstat(reader.fileno())
        shutil.copyfileobj(reader, writer)
        after = os.fstat(reader.fileno())
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ConfigurationError("工具在复制时发生改变：" + str(source))
    if destination.stat().st_nlink != 1:
        raise ConfigurationError("隔离工具副本不是独立文件：" + str(destination))


def copy_directory(source, destination, *, exclude=()):
    source = Path(source)
    if source.lstat().st_file_attributes & 0x400:
        raise ConfigurationError("工具副本不接受目录链接：" + str(source))
    destination.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir()):
        if child.name in exclude:
            continue
        if child.is_dir():
            copy_directory(child, destination / child.name, exclude=exclude)
        else:
            copy_file(child, destination / child.name)


def selected_tools():
    if os.name != "nt" or sys.version_info[:2] != (3, 11):
        raise ConfigurationError("受限执行当前要求 Windows 和 Python 3.11。")
    result = {"python": Path(sys.base_prefix) / "python.exe"}
    for name, command in (("pwsh", "pwsh.exe"), ("git", "git.exe"), ("uv", "uv.exe")):
        found = shutil.which(command)
        if not found:
            raise ConfigurationError(f"缺少 {command}；请安装后重新运行 sandbox setup。")
        result[name] = Path(found)
    if result["git"].parent.name.lower() == "cmd":
        result["git"] = result["git"].parent.parent / "mingw64" / "bin" / "git.exe"
    if not all(path.is_file() for path in result.values()):
        raise ConfigurationError("需要实际 Python 3.11、PowerShell 7、Git for Windows 和 uv。")
    return result


def build_bundle(destination, selected):
    if destination.exists():
        raise ConfigurationError("隔离副本必须使用新目录。")
    destination.mkdir(parents=True)
    python = destination / "python"
    python.mkdir()
    base = selected["python"].parent
    for item in base.iterdir():
        if item.is_file() and item.suffix.lower() in {".exe", ".dll"}:
            copy_file(item, python / item.name)
    for name in ("Lib", "DLLs"):
        copy_directory(base / name, python / name,
                       exclude=("site-packages", "__pycache__", "test"))
    modules = destination / "modules"
    import agent_adapters
    import agent_contracts
    import agent_runtime
    import agent_subsystems
    import pathspec
    for package in (agent_adapters, agent_contracts, agent_runtime, agent_subsystems, pathspec):
        source = Path(next(iter(package.__path__)))
        copy_directory(source, modules / package.__name__, exclude=("__pycache__",))
    (destination / "worker.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "sys.path.insert(0,str(Path(__file__).parent/'modules'))\n"
        "from agent_adapters.local.restricted_worker import main\nmain()\n", encoding="utf-8",
    )
    pwsh = destination / "pwsh"
    pwsh.mkdir()
    for item in selected["pwsh"].parent.iterdir():
        if item.is_file():
            copy_file(item, pwsh / item.name)
    for name in ("Modules", "en-US"):
        copy_directory(selected["pwsh"].parent / name, pwsh / name)
    git = destination / "git"
    git.mkdir()
    for item in selected["git"].parent.iterdir():
        if item.is_file() and (item.suffix.lower() == ".dll" or item.name == "git.exe"):
            copy_file(item, git / item.name)
    copy_file(selected["uv"], destination / "uv.exe")
    return {"python": str(python / "python.exe"), "pwsh": str(pwsh / "pwsh.exe"),
            "git": str(git / "git.exe"), "uv": str(destination / "uv.exe")}


def content_manifest(root):
    result = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            result[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def read_setup(home, *, allow_pending=False):
    path = home / "sandbox" / "setup.json"
    if not path.exists():
        raise ConfigurationError("受限环境尚未初始化；请运行 agenthub sandbox setup。")
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("state") != "ready" and not allow_pending:
        raise ConfigurationError("初始化未确认完成；请运行 agenthub sandbox setup --apply。")
    manifest = DependencyManifest(Path(value["bundle"]), value["manifest"], value["digest"])
    manifest.verify()
    return value, manifest


def save_setup(home, value):
    path = home / "sandbox" / "setup.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(".pending")
    with pending.open("w", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(pending, path)
