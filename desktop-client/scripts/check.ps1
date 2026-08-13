$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
$hostLine = (& rustc -vV | Select-String "^host:").Line
if (-not $hostLine) {
    throw "Unable to determine the Rust target triple."
}
$targetTriple = $hostLine.Substring("host:".Length).Trim()
$extension = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".exe" } else { "" }
$binaryRoot = Join-Path $desktopRoot "src-tauri\binaries"
$targetBinary = Join-Path $binaryRoot "agenthub-backend-$targetTriple$extension"
$placeholderCreated = $false

if (-not (Test-Path -LiteralPath $targetBinary)) {
    New-Item -ItemType Directory -Path $binaryRoot -Force | Out-Null
    New-Item -ItemType File -Path $targetBinary -Force | Out-Null
    $placeholderCreated = $true
}

Push-Location $desktopRoot
try {
    & cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    & cargo check --manifest-path src-tauri/Cargo.toml
    exit $LASTEXITCODE
}
finally {
    Pop-Location
    if ($placeholderCreated) {
        Remove-Item -LiteralPath $targetBinary -Force -ErrorAction SilentlyContinue
    }
}
