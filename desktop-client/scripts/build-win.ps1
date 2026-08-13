$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot

Push-Location $desktopRoot
try {
    if (-not (Test-Path -LiteralPath "node_modules")) {
        & pnpm install --frozen-lockfile
        if ($LASTEXITCODE -ne 0) {
            throw "Desktop dependency installation failed with exit code $LASTEXITCODE."
        }
    }
    & pnpm exec tauri build --bundles nsis
    if ($LASTEXITCODE -ne 0) {
        throw "Tauri build failed with exit code $LASTEXITCODE."
    }
    Write-Host "AgentHub Tauri installer completed." -ForegroundColor Green
    Get-ChildItem -Path "src-tauri\target\release\bundle\nsis" -File |
        Select-Object FullName, Length
}
finally {
    Pop-Location
}
