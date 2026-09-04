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
    $wheel = Get-ChildItem -LiteralPath (Join-Path $repoRoot "dist") -Filter "agenthub_system-*.whl" |
        Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1
    $env:UV_TOOL_DIR = Join-Path $validationRoot "tools"
    $env:UV_TOOL_BIN_DIR = Join-Path $validationRoot "bin"
    & uv tool install --python 3.11 --force "$($wheel.FullName)[cli]"
    if ($LASTEXITCODE -ne 0) { throw "Isolated uv tool installation failed." }
    $env:AGENTHUB_TEST_PYTHON = Join-Path $env:UV_TOOL_DIR "agenthub-system\Scripts\python.exe"
    & (Join-Path $env:UV_TOOL_BIN_DIR "agenthub.exe") --help
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI command failed." }
    & (Join-Path $repoRoot ".venv\Scripts\python.exe") -B -m pytest -q tests/test_cli_integration.py `
        --junitxml (Join-Path $validationRoot "installed-cli.xml")
    if ($LASTEXITCODE -ne 0) { throw "Installed CLI acceptance failed." }
    Write-Host "Validated installed wheel: $($wheel.FullName)"
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], "Process")
    }
    Pop-Location
}
