$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $desktopRoot

& powershell -ExecutionPolicy Bypass -File (Join-Path $PSScriptRoot "build-sidecar.ps1") -SkipIfFresh
if ($LASTEXITCODE -ne 0) {
    throw "Sidecar preparation failed with exit code $LASTEXITCODE."
}

Push-Location (Join-Path $repoRoot "frontend")
try {
    & pnpm build --mode desktop
    if ($LASTEXITCODE -ne 0) {
        throw "Frontend build failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
