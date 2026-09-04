[CmdletBinding()]
param(
    [switch]$SkipIfFresh
)

$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
$repoRoot = Split-Path -Parent $desktopRoot
$backendRoot = Join-Path $repoRoot "backend"
$sharedRoot = Join-Path $repoRoot "src"
$binaryRoot = Join-Path $desktopRoot "src-tauri\binaries"
$buildRoot = Join-Path $desktopRoot ".build-sidecar"

$hostLine = (& rustc -vV | Select-String "^host:").Line
if (-not $hostLine) {
    throw "Unable to determine the Rust target triple."
}
$targetTriple = $hostLine.Substring("host:".Length).Trim()
$extension = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".exe" } else { "" }
$targetBinary = Join-Path $binaryRoot "agenthub-backend-$targetTriple$extension"

$sourceInputs = Get-ChildItem -LiteralPath @((Join-Path $backendRoot "src"), (Join-Path $backendRoot "alembic"), $sharedRoot) -Recurse -File |
    Where-Object { $_.Extension -eq ".py" } | Select-Object -ExpandProperty FullName
$inputPaths = @($sourceInputs) + @(Get-ChildItem -LiteralPath $backendRoot -Filter "*.py" -File | Select-Object -ExpandProperty FullName) + @(
    $PSCommandPath, (Join-Path $repoRoot "pyproject.toml"), (Join-Path $repoRoot "uv.lock"), (Join-Path $backendRoot "pyproject.toml")
)
$fingerprintText = ($inputPaths | Sort-Object | ForEach-Object { "$($_):$((Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash)" }) -join "`n"
$hashAlgorithm = [Security.Cryptography.SHA256]::Create()
$fingerprint = [BitConverter]::ToString($hashAlgorithm.ComputeHash([Text.Encoding]::UTF8.GetBytes($fingerprintText))).Replace("-", "")
$hashAlgorithm.Dispose()
$fingerprintPath = "$targetBinary.inputs.sha256"
if ($SkipIfFresh -and (Test-Path -LiteralPath $targetBinary) -and (Test-Path -LiteralPath $fingerprintPath)) {
    if ((Get-Content -LiteralPath $fingerprintPath -Raw).Trim() -eq $fingerprint) {
        Write-Host "AgentHub backend sidecar is up to date: $targetBinary"
        exit 0
    }
}

New-Item -ItemType Directory -Force -Path $binaryRoot, $buildRoot | Out-Null
$separator = [IO.Path]::PathSeparator
$alembicData = "$(Join-Path $backendRoot 'alembic')${separator}alembic"
$arguments = @(
    "run",
    "--isolated",
    "--frozen",
    "--project", $backendRoot,
    "--package", "agenthub-backend",
    "--with", "pyinstaller==6.22.0",
    "pyinstaller",
    "--noconfirm",
    "--clean",
    "--onefile",
    "--name", "agenthub-backend",
    "--paths", (Join-Path $backendRoot "src"),
    "--paths", $sharedRoot,
    "--paths", $backendRoot,
    "--add-data", $alembicData,
    "--collect-all", "rapidocr_onnxruntime",
    "--collect-all", "onnxruntime",
    "--collect-submodules", "passlib.handlers",
    "--hidden-import", "app.main",
    "--hidden-import", "model_provider.providers.openai_compatible",
    "--hidden-import", "model_provider.providers.deepseek",
    "--hidden-import", "model_provider.providers.ark",
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
Set-Content -LiteralPath $fingerprintPath -Value $fingerprint -Encoding utf8
Write-Host "AgentHub backend sidecar ready: $targetBinary" -ForegroundColor Green
