$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot

& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build-sidecar.ps1") -SkipIfFresh
if ($LASTEXITCODE -ne 0) {
    throw "Sidecar preparation failed with exit code $LASTEXITCODE."
}

Push-Location $desktopRoot
try {
    & pnpm exec tauri dev
    exit $LASTEXITCODE
}
finally {
    Pop-Location
}
