[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$saved = @{}
foreach ($name in @("UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "AGENTHUB_LEGACY_PYTHON", "AGENTHUB_TEST_PYTHON", "AGENTHUB_LEGACY_SCHEMA")) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
Push-Location $repo
try {
    $env:AGENTHUB_TEST_PYTHON = Join-Path $repo "var\cli-install-validation\tools\agenthub-system\Scripts\python.exe"
    $installedVersion = (& $env:AGENTHUB_TEST_PYTHON -B -m agent_cli.main --version) -replace '^agenthub ', ''
    if ($installedVersion -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid installed version" }
    foreach ($legacyVersion in @("0.1.0", "0.1.7", "0.2.0")) {
        $env:UV_TOOL_DIR = Join-Path $repo "var\cli-upgrade-$legacyVersion\tools"
        $env:UV_TOOL_BIN_DIR = Join-Path $repo "var\cli-upgrade-$legacyVersion\bin"
        $oldWheel = Join-Path $repo "dist\agenthub_system-$legacyVersion-py3-none-any.whl"
        & uv tool install --python 3.11 --force "$oldWheel[cli]"
        if ($LASTEXITCODE -ne 0) { throw "Legacy wheel install failed" }
        $env:AGENTHUB_LEGACY_PYTHON = Join-Path $env:UV_TOOL_DIR "agenthub-system\Scripts\python.exe"
        $env:AGENTHUB_LEGACY_SCHEMA = switch ($legacyVersion) {
            "0.1.0" { "1" }
            "0.1.7" { "2" }
            "0.2.0" { "3" }
        }
        $temporary = Join-Path $repo "var\upgrade-$legacyVersion-$([guid]::NewGuid().ToString('N'))"
        & .venv\Scripts\python.exe -B -m pytest -q tests/test_harness_upgrade.py `
            "--basetemp=$temporary" "--junitxml=var/upgrade-$legacyVersion-to-$installedVersion.xml"
        if ($LASTEXITCODE -ne 0) { throw "Installed upgrade acceptance failed" }
    }
} finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
    Pop-Location
}
