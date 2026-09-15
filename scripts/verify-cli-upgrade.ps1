[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$saved = @{}
foreach ($name in @("UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "AGENTHUB_LEGACY_PYTHON", "AGENTHUB_TEST_PYTHON")) {
    $saved[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
Push-Location $repo
try {
    $env:UV_TOOL_DIR = Join-Path $repo "var\cli-upgrade-old\tools"
    $env:UV_TOOL_BIN_DIR = Join-Path $repo "var\cli-upgrade-old\bin"
    $oldWheel = Join-Path $repo "dist\agenthub_system-0.1.0-py3-none-any.whl"
    & uv tool install --python 3.11 --force "$oldWheel[cli]"
    if ($LASTEXITCODE -ne 0) { throw "Legacy wheel install failed" }
    $env:AGENTHUB_LEGACY_PYTHON = Join-Path $env:UV_TOOL_DIR "agenthub-system\Scripts\python.exe"
    $env:AGENTHUB_TEST_PYTHON = Join-Path $repo "var\cli-install-validation\tools\agenthub-system\Scripts\python.exe"
    $installedVersion = (& $env:AGENTHUB_TEST_PYTHON -B -m agent_cli.main --version) -replace '^agenthub ', ''
    if ($installedVersion -notmatch '^0\.1\.\d+$') { throw "Invalid installed version" }
    & .venv\Scripts\python.exe -B -m pytest -q tests/test_harness_upgrade.py "--junitxml=var/upgrade-$installedVersion.xml"
    if ($LASTEXITCODE -ne 0) { throw "Installed upgrade acceptance failed" }
} finally {
    foreach ($name in $saved.Keys) { [Environment]::SetEnvironmentVariable($name, $saved[$name], "Process") }
    Pop-Location
}
