"""Selected offline tools tested with the SAME business capability before/after withdrawal."""

import base64
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

from .movement import evaluate
from .standing import environment


def prepare(runtime, root):
    pwsh = Path(shutil.which("pwsh.exe") or "")
    uv = Path(shutil.which("uv.exe") or "")
    git = Path(shutil.which("git.exe") or "").parent.parent / "mingw64/bin/git.exe"
    if not all(path.is_file() for path in (pwsh, uv, git)):
        raise RuntimeError("Explicit PowerShell 7, uv and Git for Windows installations are required")
    for name, source in (("pwsh", pwsh.parent), ("git", git.parent)):
        destination = runtime / name
        destination.mkdir()
        for item in source.iterdir():
            if item.is_file() and (name == "pwsh" or item.suffix.lower() == ".dll"
                                   or item.name == "git.exe"):
                shutil.copyfile(item, destination / item.name)
        if name == "pwsh":
            for folder in ("Modules", "en-US"):
                shutil.copytree(source / folder, destination / folder)
    shutil.copyfile(uv, runtime / "uv.exe")
    for folder in ("Work", "Archive", "Private"):
        for command in ([str(git), "init", "--template=", str(root / folder)],
                        [str(git), "-C", str(root / folder), "add", "sample.txt"]):
            subprocess.run(command, capture_output=True, check=True, timeout=10,
                           env={**environment(runtime), "GIT_CONFIG_NOSYSTEM": "1",
                                "GIT_CONFIG_GLOBAL": "NUL", "GIT_TERMINAL_PROMPT": "0"})
    return {
        "sources": {"pwsh": str(pwsh), "uv": str(uv), "git": str(git)},
        "files": {str(path.relative_to(runtime)): hashlib.sha256(path.read_bytes()).hexdigest()
                  for path in runtime.rglob("*") if path.is_file()},
    }


def run(profile, generation, sid, runtime, root, scratch, *, readonly=False, namespace=None):
    python = runtime / "python/python.exe"
    git = runtime / "git/git.exe"
    env = environment(scratch)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL",
                "GIT_TERMINAL_PROMPT": "0", "UV_CACHE_DIR": str(scratch / "uv-cache"),
                "UV_PYTHON_INSTALL_DIR": str(scratch / "python"), "UV_PYTHON_DOWNLOADS": "never",
                "PIP_NO_INPUT": "1", "PYTHONNOUSERSITE": "1"})
    payload = ["-I", "-S", "-B", str(runtime / "payload.py"), "--root", str(root)]
    ps = """
$ErrorActionPreference='Stop'
$results=@{}
function Probe($name,[scriptblock]$action) {
    try { $results[$name]=@{allowed=$true;value=(& $action)} }
    catch {
        $errorObject=$_.Exception
        while($null -ne $errorObject.InnerException) { $errorObject=$errorObject.InnerException }
        $results[$name]=@{allowed=$false;winerror=($errorObject.HResult -band 65535)}
    }
}
"""
    for name, folder in (("work", "Work"), ("archive", "Archive"), ("private", "Private")):
        path = str(root / folder / "sample.txt").replace("'", "''")
        ps += f"\nProbe read_{name} {{ [IO.File]::ReadAllText('{path}') }}"
        ps += f"\nProbe write_{name} {{ [IO.File]::WriteAllText('{path}','changed'); 7 }}"
    ps += "\n$results | ConvertTo-Json -Compress"
    commands = {
        "python_version": [str(python), "--version"],
        "pwsh_version": [str(runtime / "pwsh/pwsh.exe"), "-NoLogo", "-NoProfile",
                         "-NonInteractive", "-Command", "$PSVersionTable.PSVersion.ToString()"],
        "uv_version": [str(runtime / "uv.exe"), "--version"],
        "git_version": [str(git), "--version"],
        "python": [str(python), *payload],
        "pwsh": [str(runtime / "pwsh/pwsh.exe"), "-NoLogo", "-NoProfile", "-NonInteractive",
                 "-EncodedCommand", base64.b64encode(ps.encode("utf-16-le")).decode("ascii")],
        "uv": [str(runtime / "uv.exe"), "--no-config", "run", "--offline", "--no-project",
               "--no-managed-python", "--python", str(python), "--", "python", *payload],
        "git_read_archive": [str(git), "-C", str(root / "Archive"), "show", ":sample.txt"],
        "git_read_work": [str(git), "-C", str(root / "Work"), "show", ":sample.txt"],
        "git_write_archive": [str(git), "-C", str(root / "Archive"), "config", "--local",
                              "agenthub.probe", "changed"],
        "git_write_work": [str(git), "-C", str(root / "Work"), "config", "--local",
                           "agenthub.probe", "changed"],
        "git_read_private": [str(git), "-C", str(root / "Private"), "show", ":sample.txt"],
        "git_diff": [str(git), "-C", str(root / "Work"), "diff", "--", "sample.txt"],
    }
    outcomes, checks = {}, {}
    for name, command in commands.items():
        if name in {"python", "pwsh", "uv"}:
            (root / "Work/sample.txt").write_text("work", encoding="utf-8")
        result = profile.run(command, scratch, environment=env, registry_read=True,
                             instrumentation=True, policy_experiment=generation,
                             namespace_experiment=namespace, timeout=20)
        outcomes[name] = {"argv": command, **result}
        if name in {"python", "pwsh", "uv"}:
            items = evaluate(result, sid)
            if readonly:
                denied = json.loads(result["stdout"]).get("write_work", {})
                items["write_work"] = denied.get("allowed") is False and (
                    denied.get("winerror") == 5 or denied.get("errno") == 13)
            checks[name] = all(items.values())
    for name in ("python", "pwsh", "uv", "git"):
        item = outcomes[name + "_version"]
        checks[name + "_version"] = good(item, sid) and bool(item["stdout"].strip())
    checks["pwsh_version"] &= outcomes["pwsh_version"]["stdout"].strip().startswith("7.")
    for name, expected in (("git_read_archive", "archive"), ("git_read_work", "work")):
        checks[name] = good(outcomes[name], sid) and outcomes[name]["stdout"].strip() == expected
    positive = checks["git_read_archive"] and checks["git_read_work"]
    for name in ("git_write_archive", "git_read_private", "git_write_work"):
        if name == "git_write_work" and not readonly:
            checks[name] = good(outcomes[name], sid)
            positive = positive and checks[name]
            continue
        item = outcomes[name]
        checks[name] = positive and good(item, sid, success=False) and (
            item["exit_code"] in (128, 255, 3, 4) and "Permission denied" in item["stderr"])
    checks["git_diff"] = good(outcomes["git_diff"], sid) and (
        outcomes["git_diff"]["stdout"] == "" if readonly else
        all(text in outcomes["git_diff"]["stdout"] for text in ("-work", "+changed")))
    return {"results": outcomes, "checks": checks, "passed": all(checks.values())}


def good(outcome, sid, *, success=True):
    token = outcome.get("token", {})
    return (isinstance(outcome.get("exit_code"), int)
            and (not success or outcome["exit_code"] == 0)
            and outcome.get("timed_out") is False and outcome.get("cancelled") is False
            and outcome.get("job_drained") is True and outcome.get("truncated") is False
            and outcome.get("pipe_errors") == []
            and all(token.get(key) is True for key in ("appcontainer", "lpac", "sid_matches"))
            and sid in token.get("capabilities", []))
