[CmdletBinding()]
param()
$ErrorActionPreference = "Stop"
$savedEnvironment = @{}
foreach ($name in @("UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "AGENTHUB_TEST_PYTHON")) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, "Process")
}
$repoRoot = Split-Path -Parent $PSScriptRoot
$validationRoot = Join-Path $repoRoot "var\cli-install-validation"
New-Item -ItemType Directory -Force -Path $validationRoot | Out-Null
Push-Location $repoRoot
try {
    & uv build --package agenthub-system --wheel
    if ($LASTEXITCODE -ne 0) { throw "CLI wheel build failed." }
    $wheelPath = & (Join-Path $repoRoot ".venv\Scripts\python.exe") -B scripts/verify-system-wheel.py
    if ($LASTEXITCODE -ne 0) { throw "Wheel version/source verification failed." }
    $env:UV_TOOL_DIR = Join-Path $validationRoot "tools"
    $env:UV_TOOL_BIN_DIR = Join-Path $validationRoot "bin"
    & uv tool install --python 3.11 --force "$wheelPath[cli]"
    if ($LASTEXITCODE -ne 0) { throw "Isolated uv tool installation failed." }
    $env:AGENTHUB_TEST_PYTHON = Join-Path $env:UV_TOOL_DIR "agenthub-system\Scripts\python.exe"
    & (Join-Path $env:UV_TOOL_BIN_DIR "agenthub.exe") --help
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI command failed." }
    & (Join-Path $repoRoot ".venv\Scripts\python.exe") -B -m pytest -q tests/test_cli_integration.py tests/test_harness_diagnostics.py tests/test_memory_cli.py `
        --basetemp (Join-Path $validationRoot "tests-$([guid]::NewGuid().ToString('N'))") `
        --junitxml (Join-Path $validationRoot "installed-cli.xml")
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI acceptance failed." }
    Write-Host "Validated installed wheel: $wheelPath"
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    Pop-Location
}
