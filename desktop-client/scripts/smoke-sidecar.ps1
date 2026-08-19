$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
$hostLine = (& rustc -vV | Select-String "^host:").Line
$targetTriple = $hostLine.Substring("host:".Length).Trim()
$extension = if ($IsWindows -or $env:OS -eq "Windows_NT") { ".exe" } else { "" }
$binary = Join-Path $desktopRoot "src-tauri\binaries\agenthub-backend-$targetTriple$extension"
if (-not (Test-Path -LiteralPath $binary)) {
    throw "Desktop sidecar is missing. Run pnpm build:sidecar first."
}

$listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$listener.Start()
$port = $listener.LocalEndpoint.Port
$listener.Stop()

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$dataDir = Join-Path $tempRoot "agenthub-tauri-smoke-$([guid]::NewGuid().ToString('N'))"
$stdout = Join-Path $dataDir "sidecar.stdout.log"
$stderr = Join-Path $dataDir "sidecar.stderr.log"
$sessionToken = "a" * 64
New-Item -ItemType Directory -Path $dataDir | Out-Null

$process = Start-Process -FilePath $binary `
    -ArgumentList @(
        "--data-dir", $dataDir,
        "--port", $port,
        "--session-token", $sessionToken
    ) `
    -PassThru `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdout `
    -RedirectStandardError $stderr

try {
    $healthy = $false
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        if ($process.HasExited) {
            break
        }
        try {
            $response = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 1
            if ($response.status -eq "ok") {
                $healthy = $true
                break
            }
        }
        catch {
            Start-Sleep -Milliseconds 250
        }
    }
    if (-not $healthy) {
        $errorText = if (Test-Path -LiteralPath $stderr) { Get-Content -LiteralPath $stderr -Raw } else { "" }
        throw "Desktop sidecar did not become healthy. $errorText"
    }
    foreach ($required in @("agenthub.db", "desktop-secrets.json")) {
        if (-not (Test-Path -LiteralPath (Join-Path $dataDir $required))) {
            throw "Desktop sidecar did not create $required."
        }
    }
    $headers = @{ "X-AgentHub-Desktop-Session" = $sessionToken }
    $me = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$port/api/v1/auth/me" `
        -Headers $headers `
        -TimeoutSec 5
    if (-not $me.data.id) {
        throw "Desktop sidecar did not initialize the local identity."
    }
    $loginStatus = $null
    try {
        Invoke-WebRequest `
            -Uri "http://127.0.0.1:$port/api/v1/auth/login" `
            -Method Post `
            -ContentType "application/json" `
            -Body '{"username_or_email":"unused","password":"unused"}' `
            -TimeoutSec 5 | Out-Null
        $loginStatus = 200
    }
    catch {
        $loginStatus = [int]$_.Exception.Response.StatusCode
    }
    if ($loginStatus -ne 404) {
        throw "Desktop account login must be unavailable; received $loginStatus."
    }
    Write-Host "Desktop sidecar migration and single-user identity passed on port $port." -ForegroundColor Green
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force
        $process.WaitForExit()
    }
    $resolvedData = [IO.Path]::GetFullPath($dataDir)
    if ($resolvedData.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedData -Recurse -Force -ErrorAction SilentlyContinue
    }
}
