[CmdletBinding()]
param(
    [switch]$SkipIfFresh
)

$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $desktopRoot
$backendRoot = Join-Path $repoRoot "backend"
$binaryRoot = Join-Path $desktopRoot "src-tauri\binaries"
$buildRoot = Join-Path $desktopRoot ".build-sidecar"

$hostLine = (& rustc -vV | Select-String "^host:").Line
if (-not $hostLine) {
    throw "Unable to determine the Rust target triple."
}
$targetTriple = $hostLine.Substring("host:".Length).Trim()
$extension = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".exe" } else { "" }
$targetBinary = Join-Path $binaryRoot "agenthub-backend-$targetTriple$extension"

if ($SkipIfFresh -and (Test-Path -LiteralPath $targetBinary)) {
    $backendInputs = Get-ChildItem -LiteralPath $backendRoot -Recurse -File |
        Where-Object { $_.Extension -in ".py", ".toml", ".lock" } |
        Select-Object -ExpandProperty FullName
    $inputPaths = @($backendInputs) + @($PSCommandPath)
    $latestInput = $inputPaths |
        Get-Item |
        Sort-Object LastWriteTimeUtc -Descending |
        Select-Object -First 1
    if ($latestInput -and (Get-Item -LiteralPath $targetBinary).LastWriteTimeUtc -ge $latestInput.LastWriteTimeUtc) {
        Write-Host "AgentHub backend sidecar is up to date: $targetBinary"
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $binaryRoot, $buildRoot | Out-Null
$separator = [IO.Path]::PathSeparator
$alembicData = "$(Join-Path $backendRoot 'alembic')${separator}alembic"
$arguments = @(
    "run",
    "--project", $backendRoot,
    "--with", "pyinstaller==6.22.0",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "agenthub-backend",
    "--paths", (Join-Path $backendRoot "src"),
    "--paths", $backendRoot,
    "--add-data", $alembicData,
    "--collect-all", "rapidocr_onnxruntime",
    "--collect-all", "onnxruntime",
    "--collect-submodules", "passlib.handlers",
    "--hidden-import", "app.main",
    "--hidden-import", "db.base",
    "--hidden-import", "db.models",
    "--hidden-import", "aiosqlite",
    "--hidden-import", "email_validator",
    "--hidden-import", "passlib.handlers.bcrypt",
    "--hidden-import", "uvicorn.logging",
    "--hidden-import", "uvicorn.loops.auto",
    "--hidden-import", "uvicorn.protocols.http.auto",
    "--hidden-import", "uvicorn.protocols.websockets.auto",
    "--hidden-import", "uvicorn.lifespan.on",
    "--distpath", (Join-Path $buildRoot "dist"),
    "--workpath", (Join-Path $buildRoot "work"),
    "--specpath", (Join-Path $buildRoot "spec"),
    (Join-Path $backendRoot "desktop_entry.py")
)

& uv @arguments
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

$builtBinary = Join-Path $buildRoot "dist\agenthub-backend$extension"
if (-not (Test-Path -LiteralPath $builtBinary)) {
    throw "PyInstaller did not produce $builtBinary."
}
Copy-Item -LiteralPath $builtBinary -Destination $targetBinary -Force
Write-Host "AgentHub backend sidecar ready: $targetBinary" -ForegroundColor Green
