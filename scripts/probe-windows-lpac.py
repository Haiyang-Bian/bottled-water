"""P1 native experiment only: no model, no production state or CLI activation."""

import argparse
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import traceback
from uuid import uuid4

from lpac_probe.native import LpacProfile
from lpac_probe.validation import evaluate


def fixture_tree(root):
    """Do not follow links or remove ACEs through filesystem aliases."""
    info = root.lstat()
    if info.st_file_attributes & 0x400 or (root.is_file() and info.st_nlink > 1):
        raise RuntimeError("Fixture contains a reparse point or multiply linked file")
    yield root
    if root.is_dir():
        with os.scandir(root) as entries:
            children = sorted((Path(entry.path) for entry in entries), key=lambda p: p.name)
        for child in children:
            yield from fixture_tree(child)


def cleanup(profile, manifest, output, save):
    import win32security as security

    if profile.cleanup_blocked:
        manifest["cleanup_blocked"] = True
        save()
        raise RuntimeError("Retain fixture ACLs and profile: Job cleanup is unconfirmed")
    for grant in manifest["grants"]:
        path = Path(grant["path"])
        if path not in tuple(output / "fixture" / name for name in ("A", "B", "R")):
            raise RuntimeError("Unexpected path in probe recovery ledger")
        for target in fixture_tree(path):
            sd = security.GetNamedSecurityInfo(
                str(target), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
            )
            acl = sd.GetSecurityDescriptorDacl()
            for index in reversed(range(acl.GetAceCount())):
                if security.ConvertSidToStringSid(acl.GetAce(index)[-1]) == profile.sid:
                    acl.DeleteAce(index)
            security.SetNamedSecurityInfo(
                str(target),
                security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION,
                None,
                None,
                acl,
                None,
            )
            verified = security.GetNamedSecurityInfo(
                str(target),
                security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION,
            ).GetSecurityDescriptorDacl()
            if any(
                security.ConvertSidToStringSid(verified.GetAce(i)[-1]) == profile.sid
                for i in range(verified.GetAceCount())
            ):
                raise RuntimeError("Probe ACE removal could not be verified")
        if grant["path"] not in manifest["cleanup"]:
            manifest["cleanup"].append(grant["path"])
        save()
    if not manifest.get("profile_deleted"):
        profile.delete()
        manifest["profile_deleted"] = True
        save()


def main():
    import win32security as security

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--toolchain", action="store_true")
    parser.add_argument("--registry-read", action="store_true")
    parser.add_argument("--full-toolchain", action="store_true")
    parser.add_argument("--instrumentation", action="store_true")
    parser.add_argument("--namespace-experiment")
    args = parser.parse_args()
    if ctypes.windll.shell32.IsUserAnAdmin():
        parser.error("Native tool probes must run from the ordinary user, not an elevated host")
    if args.full_toolchain and not args.toolchain:
        parser.error("--full-toolchain requires --toolchain")
    repo = Path(__file__).resolve().parents[1]
    if args.namespace_experiment is not None:
        from lpac_probe.namespace import capability_name

        capability_name(args.namespace_experiment)
        namespace_report = repo / "var" / ("l4a-namespace-" + args.namespace_experiment + ".json")
        prepared = json.loads(namespace_report.read_text(encoding="utf-8"))
        if prepared.get("status") != "ready" or not prepared.get("elevated"):
            raise RuntimeError("Namespace initialization has not been verified")
    output = Path(args.output).resolve()
    if not output.is_relative_to(repo / "var"):
        raise ValueError("Use a new output directory under this repository's var")
    if output.exists():
        raise ValueError("Output directory already exists")
    output.mkdir(parents=True)
    fixture = output / "fixture"
    fixture.mkdir()
    for name in ("A", "B", "C"):
        directory = fixture / name
        directory.mkdir()
        (directory / "sample.txt").write_text(name + "-fixture", encoding="utf-8")
    if args.toolchain:
        runtime = fixture / "R"
        runtime.mkdir()
        python = runtime / "python"
        python.mkdir()
        base = Path(sys.base_prefix)
        for pattern in ("*.exe", "*.dll"):
            for source in base.glob(pattern):
                shutil.copyfile(source, python / source.name)
        for name in ("Lib", "DLLs"):
            shutil.copytree(
                base / name,
                python / name,
                ignore=shutil.ignore_patterns("site-packages", "__pycache__", "test"),
            )
        shutil.copyfile(repo / "scripts/lpac-probe-payload.py", runtime / "payload.py")
        if args.full_toolchain:
            pwsh_source = Path(shutil.which("pwsh.exe") or "")
            git_source = Path(shutil.which("git.exe") or "").parent.parent / "mingw64/bin"
            uv_source = Path(shutil.which("uv.exe") or "")
            if not all(p.is_file() for p in (pwsh_source, uv_source, git_source / "git.exe")):
                raise RuntimeError("Expected explicit installed pwsh, uv and Git for Windows")
            pwsh = runtime / "pwsh"
            pwsh.mkdir()
            for source in pwsh_source.parent.iterdir():
                if source.is_file():
                    shutil.copyfile(source, pwsh / source.name)
            for name in ("Modules", "en-US"):
                shutil.copytree(pwsh_source.parent / name, pwsh / name)
            git = runtime / "git"
            git.mkdir()
            for source in git_source.iterdir():
                if source.suffix.lower() == ".dll" or source.name == "git.exe":
                    shutil.copyfile(source, git / source.name)
            shutil.copyfile(uv_source, runtime / "uv.exe")
            for name in ("A", "B", "C"):
                original_git = str(git_source / "git.exe")
                for command in (
                    [original_git, "init", "--template=", str(fixture / name)],
                    [original_git, "-C", str(fixture / name), "add", "sample.txt"],
                ):
                    subprocess.run(
                        command,
                        capture_output=True,
                        check=True,
                        env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": "NUL"},
                    )
    profile = LpacProfile("AgentHub.Probe." + uuid4().hex)
    manifest = {
        "profile": profile.name,
        "grants": [],
        "results": [],
        "cleanup": [],
        "os_build": str(sys.getwindowsversion()),
        "python": sys.version,
        "host_elevated": False,
        "scope": "toolchain" if args.full_toolchain else "partial_smoke",
        "namespace_experiment": args.namespace_experiment,
        "source_revision": subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo,
            text=True,
        ).strip(),
        "source_sha256": {
            str(path.relative_to(repo)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in (
                repo / "scripts/probe-windows-lpac.py",
                repo / "scripts/lpac_probe/native.py",
                repo / "scripts/lpac_probe/validation.py",
                repo / "scripts/lpac-probe-payload.py",
            )
        },
    }

    def save():
        pending = output / "probe.json.pending"
        with pending.open("w", encoding="utf-8") as file:
            file.write(json.dumps(manifest, indent=2))
            file.flush()
            os.fsync(file.fileno())
        os.replace(pending, output / "probe.json")

    save()  # Persist intent before creating any OS object.
    profile.create()
    manifest["sid"] = profile.sid
    save()
    sid = security.ConvertStringSidToSid(profile.sid)
    try:
        grants = [("A", 0x1200A9), ("B", 0x1301BF)]
        if args.toolchain:
            grants.append(("R", 0x1200A9))
        for name, mask in grants:
            path = fixture / name
            # The probe owns these fixtures; refuse aliases before touching any ACL.
            list(fixture_tree(path))
            manifest["grants"].append({"path": str(path), "mask": mask, "state": "intent"})
            save()
            sd = security.GetNamedSecurityInfo(
                str(path), security.SE_FILE_OBJECT, security.DACL_SECURITY_INFORMATION
            )
            acl = sd.GetSecurityDescriptorDacl()
            if acl is None:
                raise RuntimeError("Refuse a NULL DACL")
            acl.AddAccessAllowedAceEx(security.ACL_REVISION_DS, 3, mask, sid)
            security.SetNamedSecurityInfo(
                str(path),
                security.SE_FILE_OBJECT,
                security.DACL_SECURITY_INFORMATION,
                None,
                None,
                acl,
                None,
            )
            manifest["grants"][-1]["state"] = "applied"
            save()
        shell = str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe")
        environment = {
            "SystemRoot": os.environ["SystemRoot"],
            "WINDIR": os.environ["SystemRoot"],
            "SystemDrive": Path(os.environ["SystemRoot"]).drive,
            "ComSpec": shell,
            "PATH": str(Path(shell).parent),
            "USERPROFILE": str(fixture / "B"),
            "LOCALAPPDATA": str(fixture / "B"),
            "APPDATA": str(fixture / "B"),
            "TEMP": str(fixture / "B"),
            "TMP": str(fixture / "B"),
        }
        environment.update(
            {
                "DOTNET_EnableDiagnostics": "0",
                "POWERSHELL_TELEMETRY_OPTOUT": "1",
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "NUL",
                "GIT_TERMINAL_PROMPT": "0",
                "UV_OFFLINE": "1",
                "UV_CACHE_DIR": str(fixture / "B/uv-cache"),
            }
        )
        checks = [
            ("read_A", [shell, "/d", "/c", "type", str(fixture / "A/sample.txt")]),
            (
                "write_A",
                [shell, "/d", "/c", "echo", "changed", ">" + str(fixture / "A/sample.txt")],
            ),
            ("write_B", [shell, "/d", "/c", "echo", "changed", ">" + str(fixture / "B/new.txt")]),
            ("read_C", [shell, "/d", "/c", "type", str(fixture / "C/sample.txt")]),
        ]
        if args.toolchain:
            powershell = str(
                Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            )
            script = "$ErrorActionPreference='Stop'; [Console]::WriteLine('powershell-fixture')"
            checks.extend(
                [
                    (
                        "powershell_start",
                        [
                            powershell,
                            "-NoLogo",
                            "-NoProfile",
                            "-NonInteractive",
                            "-EncodedCommand",
                            base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
                        ],
                    ),
                    (
                        "python_matrix",
                        [
                            str(python / "python.exe"),
                            "-I",
                            "-S",
                            "-B",
                            str(runtime / "payload.py"),
                            "--fixture",
                            str(fixture),
                        ],
                    ),
                    (
                        "cwd_diagnostic",
                        [
                            str(python / "python.exe"),
                            "-I",
                            "-S",
                            "-B",
                            str(runtime / "payload.py"),
                            "--fixture",
                            str(fixture),
                            "--cwd-diagnostic",
                        ],
                    ),
                ]
            )
            if args.full_toolchain:
                ps_paths = {
                    name: str(fixture / name / "sample.txt").replace("'", "''")
                    for name in ("A", "B", "C")
                }
                ps_matrix = (
                    """
$ErrorActionPreference = 'Stop'
$results = @{}
function Probe($name, [scriptblock]$operation) {
    try { $v = & $operation; $results[$name] = @{allowed=$true; value=$v} }
    catch { $results[$name] = @{allowed=$false; error=$_.Exception.Message;
                              hresult=$_.Exception.HResult} }
}
"""
                    + f"""
Probe read_A {{Get-Content -LiteralPath '{ps_paths["A"]}'}}
Probe write_A {{Set-Content -LiteralPath '{ps_paths["A"]}' -Value changed}}
Probe write_B {{Set-Content -LiteralPath '{ps_paths["B"]}' -Value powershell-fixture}}
Probe read_C {{Get-Content -LiteralPath '{ps_paths["C"]}'}}
$results | ConvertTo-Json -Compress
"""
                )
                checks.extend(
                    [
                        (
                            "pwsh_start",
                            [
                                str(pwsh / "pwsh.exe"),
                                "-NoLogo",
                                "-NoProfile",
                                "-NonInteractive",
                                "-EncodedCommand",
                                base64.b64encode(script.encode("utf-16-le")).decode("ascii"),
                            ],
                        ),
                        ("git_start", [str(git / "git.exe"), "--version"]),
                        ("uv_start", [str(runtime / "uv.exe"), "--version"]),
                        (
                            "pwsh_matrix",
                            [
                                str(pwsh / "pwsh.exe"),
                                "-NoLogo",
                                "-NoProfile",
                                "-NonInteractive",
                                "-EncodedCommand",
                                base64.b64encode(ps_matrix.encode("utf-16-le")).decode("ascii"),
                            ],
                        ),
                        (
                            "git_read_A",
                            [str(git / "git.exe"), "-C", str(fixture / "A"), "show", ":sample.txt"],
                        ),
                        (
                            "git_write_A",
                            [
                                str(git / "git.exe"),
                                "-C",
                                str(fixture / "A"),
                                "config",
                                "--local",
                                "agenthub.probe",
                                "changed",
                            ],
                        ),
                        (
                            "git_write_B",
                            [
                                str(git / "git.exe"),
                                "-C",
                                str(fixture / "B"),
                                "config",
                                "--local",
                                "agenthub.probe",
                                "changed",
                            ],
                        ),
                        (
                            "git_read_C",
                            [str(git / "git.exe"), "-C", str(fixture / "C"), "show", ":sample.txt"],
                        ),
                        (
                            "git_diff_B",
                            [
                                str(git / "git.exe"),
                                "-C",
                                str(fixture / "B"),
                                "diff",
                                "--",
                                "sample.txt",
                            ],
                        ),
                        (
                            "uv_matrix",
                            [
                                str(runtime / "uv.exe"),
                                "--no-config",
                                "run",
                                "--offline",
                                "--no-project",
                                "--no-managed-python",
                                "--python",
                                str(python / "python.exe"),
                                "--",
                                "python",
                                "-I",
                                "-S",
                                "-B",
                                str(runtime / "payload.py"),
                                "--fixture",
                                str(fixture),
                            ],
                        ),
                    ]
                )
        for name, command in checks:
            try:
                result = profile.run(
                    command,
                    fixture / "B",
                    environment=environment,
                    registry_read=args.registry_read,
                    instrumentation=args.instrumentation,
                    namespace_experiment=args.namespace_experiment,
                )
            except Exception as exc:
                result = {
                    "error": type(exc).__name__,
                    "detail": str(exc),
                    "traceback": traceback.format_exc(),
                }
            manifest["results"].append({"name": name, **result})
            save()
            print(
                json.dumps(
                    {
                        "name": name,
                        "exit_code": result.get("exit_code"),
                        "timed_out": result.get("timed_out"),
                        "error": result.get("error"),
                    }
                ),
                flush=True,
            )
        if args.toolchain and len(manifest["results"]) == len(checks):
            listeners = []
            try:
                ports = []
                for family, address in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
                    listener = socket.socket(family, socket.SOCK_STREAM)
                    listener.bind((address, 0))
                    listener.listen(4)
                    listeners.append(listener)
                    port = listener.getsockname()[1]
                    ports.append(str(port))
                    udp = socket.socket(family, socket.SOCK_DGRAM)
                    udp.bind((address, port))
                    listeners.append(udp)
                    listener.settimeout(2)
                    udp.settimeout(2)
                    with socket.socket(family, socket.SOCK_STREAM) as client:
                        client.settimeout(2)
                        client.connect((address, port))
                        accepted, _ = listener.accept()
                        accepted.close()
                    with socket.socket(family, socket.SOCK_DGRAM) as client:
                        client.sendto(b"host-control", (address, port))
                        if udp.recvfrom(64)[0] != b"host-control":
                            raise RuntimeError("Host UDP positive control failed")
                manifest["results"].append({"name": "host_network_control", "passed": True})
                save()
                result = profile.run(
                    [
                        str(python / "python.exe"),
                        "-I",
                        "-S",
                        "-B",
                        str(runtime / "payload.py"),
                        "--fixture",
                        str(fixture),
                        "--network",
                        *ports,
                    ],
                    fixture / "B",
                    environment=environment,
                    registry_read=args.registry_read,
                    instrumentation=args.instrumentation,
                    namespace_experiment=args.namespace_experiment,
                )
                manifest["results"].append({"name": "python_network", **result})
                save()
                print(
                    json.dumps({"name": "python_network", "exit_code": result["exit_code"]}),
                    flush=True,
                )
            finally:
                for listener in listeners:
                    listener.close()
    finally:
        cleanup(profile, manifest, output, save)
    manifest["checks"] = evaluate(manifest["results"], full=args.full_toolchain)
    manifest["checks"]["A_unchanged"] = (fixture / "A/sample.txt").read_text() == "A-fixture"
    manifest["checks"]["B_changed"] = (fixture / "B/new.txt").read_text().strip() == "changed"
    if args.full_toolchain:
        manifest["checks"]["git_B_config"] = (
            "probe = changed" in (fixture / "B/.git/config").read_text()
        )
    manifest["passed"] = all(manifest["checks"].values())
    # This smoke experiment does not cover the complete adversarial P1 gate.
    manifest["p1_release_gate"] = "not_passed"
    save()
    print(json.dumps({"checks": manifest["checks"], "p1_release_gate": "not_passed"}))
    if not manifest["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
