"""Stage reviewed fixed components; elevation copies verified data before executing it."""

import base64
import json
import os
from pathlib import Path
import sys
import subprocess

from agent_contracts.errors import ConfigurationError
from .sandbox_components import copy_file, copy_directory, content_manifest


def stage_component(destination, namespace):
    destination.mkdir(parents=True)
    python = destination / "python"
    python.mkdir()
    base = Path(sys.base_prefix)
    for file in base.iterdir():
        if file.is_file() and file.suffix.lower() in {".exe", ".dll"}:
            copy_file(file, python / file.name)
    for name in ("Lib", "DLLs"):
        copy_directory(base / name, python / name,
                       exclude=("site-packages", "__pycache__", "test"))
    from . import sandbox_admin, windows_lpac, windows_namespace
    package = destination / "modules" / "agent_adapters" / "local"
    package.mkdir(parents=True)
    for module in (sandbox_admin, windows_lpac, windows_namespace):
        copy_file(Path(module.__file__), package / Path(module.__file__).name)
    import win32api
    import win32file
    import win32security
    import pywintypes
    import win32con
    for module in (win32api, win32file, win32security, pywintypes, win32con):
        copy_file(Path(module.__file__), python / Path(module.__file__).name)
    # Load the same DLL that win32file uses, not a renamed duplicate with a second
    # set of native handle types. No PATH/user-site lookup is permitted here.
    (python / "pywintypes.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "from importlib.machinery import ExtensionFileLoader, ModuleSpec\n"
        "from importlib.util import module_from_spec\n"
        "original=sys.modules[__name__]\n"
        "path=str(Path(__file__).with_name('pywintypes311.dll'))\n"
        "loader=ExtensionFileLoader(__name__,path)\n"
        "spec=ModuleSpec(name=__name__,loader=loader,origin=path)\n"
        "module=module_from_spec(spec)\nloader.exec_module(module)\n"
        "sys.modules[__name__]=original\nglobals().update(module.__dict__)\n", encoding="utf-8",
    )
    (destination / "initialize.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        "root=Path(__file__).parent\n"
        "sys.path[:0]=[str(root/'python'),str(root/'modules')]\n"
        "from agent_adapters.local.sandbox_admin import main\nmain()\n", encoding="utf-8",
    )
    (destination / "component.json").write_text(json.dumps({"namespace": namespace, "version": 1}),
                                                encoding="utf-8")
    result = subprocess.run([str(python / "python.exe"), "-I", "-S", "-B",
                             str(destination / "initialize.py"), "check"],
                            cwd=destination, capture_output=True, text=True, timeout=30)
    if result.returncode:
        raise ConfigurationError("隔离初始化组件预检失败（未提权）：" + result.stderr[-2000:])
    return content_manifest(destination)


def elevated_script(script):
    """Only fixed source constructed by this trusted module reaches the elevated shell."""
    from ctypes import Structure, byref, sizeof, windll
    from ctypes import wintypes as w
    class ExecuteInfo(Structure):
        _fields_ = [("cbSize", w.DWORD), ("fMask", w.ULONG), ("hwnd", w.HWND),
                    ("lpVerb", w.LPCWSTR), ("lpFile", w.LPCWSTR), ("lpParameters", w.LPCWSTR),
                    ("lpDirectory", w.LPCWSTR), ("nShow", w.INT), ("hInstApp", w.HINSTANCE),
                    ("lpIDList", w.LPVOID), ("lpClass", w.LPCWSTR), ("hkeyClass", w.HKEY),
                    ("dwHotKey", w.DWORD), ("hIcon", w.HANDLE), ("hProcess", w.HANDLE)]
    script = (
        "$env:PSModulePath=$PSHOME+'\\Modules'\n"
        "$env:PATH=$env:SystemRoot+'\\System32'\n"
        "Set-Location -LiteralPath $PSHOME\n" + script
    )
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    value = ExecuteInfo()
    value.cbSize, value.fMask, value.lpVerb = sizeof(value), 0x40, "runas"
    value.lpFile = str(powershell)
    value.lpParameters = "-NoProfile -NonInteractive -EncodedCommand " + encoded
    value.nShow = 0
    if not windll.shell32.ShellExecuteExW(byref(value)):
        raise ConfigurationError("管理员初始化未获批准；未启用受限环境。")
    import win32event
    import win32process
    import win32api
    try:
        result = win32event.WaitForSingleObject(value.hProcess, 120000)
        if result != 0 or win32process.GetExitCodeProcess(value.hProcess) != 0:
            raise ConfigurationError("管理员组件安装未确认成功；请运行 sandbox doctor。")
    finally:
        win32api.CloseHandle(value.hProcess)


def install_component(source, target, hashes):
    # Embed reviewed paths/hashes as JSON DATA, never executable interpolation.
    specification = base64.b64encode(json.dumps({
        "source": str(source), "target": str(target), "files": hashes,
    }).encode("utf-8")).decode("ascii")
    script = r'''
$ErrorActionPreference='Stop'
$spec=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('SPEC')) | ConvertFrom-Json
$expected=Join-Path $env:ProgramFiles 'AgentHub\Sandbox'
$target=[IO.Path]::GetFullPath($spec.target)
if (-not $target.StartsWith($expected+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid target' }
if (Test-Path -LiteralPath $target) { throw 'Target already exists' }
$parent=$target
while ($parent -and (Test-Path -LiteralPath $parent)) {
  if ((Get-Item -LiteralPath $parent -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Alias' }
  $parent=Split-Path -Parent $parent
}
foreach ($dir in @((Join-Path $env:ProgramFiles 'AgentHub'),$expected,$target)) {
  $created=$false
  if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir | Out-Null; $created=$true }
  if ((Get-Item -LiteralPath $dir -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Alias' }
  if ($created) {
    $acl=New-Object Security.AccessControl.DirectorySecurity
    $acl.SetSecurityDescriptorSddlForm('O:BAG:BAD:P(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)(A;OICI;0x1200a9;;;BU)')
    Set-Acl -LiteralPath $dir -AclObject $acl
  } else {
    $acl=Get-Acl -LiteralPath $dir
    $owner=$acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
    if ($owner -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Untrusted component owner' }
    foreach ($ace in $acl.GetAccessRules($true,$true,[Security.Principal.SecurityIdentifier])) {
      if ($ace.AccessControlType -eq 'Allow' -and ($ace.FileSystemRights.value__ -band 0xD0156) -and $ace.IdentityReference.Value -notin @('S-1-5-18','S-1-5-32-544')) { throw 'Untrusted component writer' }
    }
  }
}
foreach ($item in $spec.files.PSObject.Properties) {
  $relative=$item.Name
  $destination=[IO.Path]::GetFullPath((Join-Path $target $relative))
  if (-not $destination.StartsWith($target+'\',[StringComparison]::OrdinalIgnoreCase)) { throw 'Invalid component path' }
  $parent=Split-Path -Parent $destination
  if (-not (Test-Path -LiteralPath $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
  Copy-Item -LiteralPath (Join-Path $spec.source $relative) -Destination $destination
  if ((Get-Item -LiteralPath $destination -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Component alias' }
  if ((Get-FileHash -LiteralPath $destination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.Value) { throw 'Component hash mismatch' }
}
foreach ($entry in (Get-ChildItem -LiteralPath $target -Recurse -Force)) {
  if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Component alias' }
  $acl=Get-Acl -LiteralPath $entry.FullName
  $acl.SetOwner((New-Object Security.Principal.SecurityIdentifier('S-1-5-32-544')))
  Set-Acl -LiteralPath $entry.FullName -AclObject $acl
}
& (Join-Path $target 'python\python.exe') -I -S -B (Join-Path $target 'initialize.py') setup
exit $LASTEXITCODE
'''.replace("SPEC", specification)
    if len(script.encode("utf-16-le")) > 20000:
        # Large manifests cannot be safely squeezed into the Windows command line.
        # Pin one manifest hash, then validate its contents before any protected execution.
        import hashlib
        manifest = source / "install-manifest.json"
        manifest.write_text(json.dumps({"source": str(source), "target": str(target), "files": hashes}),
                            encoding="utf-8")
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        path64 = base64.b64encode(str(manifest).encode("utf-8")).decode("ascii")
        start = script.index("$spec=")
        end = script.index("\n$expected", start)
        script = script[:start] + (
            "$manifest=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('" + path64 + "'))\n"
            "$bytes=[IO.File]::ReadAllBytes($manifest)\n"
            "$sha=[Security.Cryptography.SHA256]::Create()\n"
            "$actual=([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-','').ToLowerInvariant()\n"
            "if ($actual -ne '" + digest + "') { throw 'Manifest changed' }\n"
            "$spec=[Text.Encoding]::UTF8.GetString($bytes) | ConvertFrom-Json"
        ) + script[end:]
    elevated_script(script)


def invoke_component(target, action, hashes):
    if action not in {"setup", "remove"}:
        raise ValueError("Invalid initializer action")
    from .sandbox_admin import verify_component
    verify_component(target, hashes)
    # Protected paths are data inside an encoded script.
    value = base64.b64encode(str(target).encode("utf-8")).decode("ascii")
    elevated_script("$ErrorActionPreference='Stop'; $p=[Text.Encoding]::UTF8.GetString("
                    "[Convert]::FromBase64String('" + value + "')); & (Join-Path $p "
                    "'python\\python.exe') -I -S -B (Join-Path $p 'initialize.py') "
                    + action + "; exit $LASTEXITCODE")
