$ErrorActionPreference = "Stop"
$desktopRoot = Split-Path -Parent $PSScriptRoot
$appBinary = Join-Path $desktopRoot "src-tauri\target\release\agenthub-desktop.exe"
if (-not (Test-Path -LiteralPath $appBinary)) {
    throw "Tauri release executable is missing. Run pnpm build:win first."
}

$tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$dataDir = Join-Path $tempRoot "agenthub-tauri-app-smoke-$([guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $dataDir | Out-Null
$env:AGENTHUB_DESKTOP_DATA_DIR = $dataDir
$appProcess = Start-Process -FilePath $appBinary -PassThru

function Get-DescendantProcessIds([int]$ParentId) {
    $result = @()
    $pending = @($ParentId)
    while ($pending.Count -gt 0) {
        $current = $pending[0]
        $pending = @($pending | Select-Object -Skip 1)
        $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $current")
        foreach ($child in $children) {
            $result += [int]$child.ProcessId
            $pending += [int]$child.ProcessId
        }
    }
    return @($result | Select-Object -Unique)
}

try {
    $healthy = $false
    $sidecarPids = @()
    for ($attempt = 0; $attempt -lt 160; $attempt++) {
        if ($appProcess.HasExited) { break }
        $sidecarPids = @(Get-DescendantProcessIds $appProcess.Id)
        foreach ($pidValue in $sidecarPids) {
            $connections = @(Get-NetTCPConnection -State Listen -OwningProcess $pidValue -ErrorAction SilentlyContinue)
            foreach ($connection in $connections) {
                try {
                    $response = Invoke-RestMethod -Uri "http://127.0.0.1:$($connection.LocalPort)/health" -TimeoutSec 1
                    if ($response.status -eq "ok") {
                        $healthy = $true
                        break
                    }
                }
                catch {}
            }
            if ($healthy) { break }
        }
        if ($healthy) { break }
        Start-Sleep -Milliseconds 250
    }
    if (-not $healthy) {
        throw "Tauri application did not start a healthy backend sidecar."
    }
    $firstInstanceDescendants = @(Get-DescendantProcessIds $appProcess.Id)
    $secondInstance = Start-Process -FilePath $appBinary -PassThru
    if (-not $secondInstance.WaitForExit(5000)) {
        Stop-Process -Id $secondInstance.Id -Force -ErrorAction SilentlyContinue
        throw "A second Tauri instance remained active instead of focusing the first window."
    }
    $secondInstanceDescendants = @(Get-DescendantProcessIds $secondInstance.Id)
    if ($secondInstanceDescendants.Count -gt 0) {
        throw "The rejected second instance created child processes."
    }
    $sidecarPids = @(Get-DescendantProcessIds $appProcess.Id)
    if ($sidecarPids.Count -ne $firstInstanceDescendants.Count) {
        throw "The second launch changed the managed sidecar process set."
    }
    $null = $appProcess.CloseMainWindow()
    if (-not $appProcess.WaitForExit(5000)) {
        throw "The Tauri application did not exit after closing its main window."
    }
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        $remainingPids = @($sidecarPids | Where-Object {
            Get-Process -Id $_ -ErrorAction SilentlyContinue
        })
        if ($remainingPids.Count -eq 0) { break }
        Start-Sleep -Milliseconds 250
    }
    if ($remainingPids.Count -gt 0) {
        throw "Closing the Tauri application left managed child processes running: $remainingPids"
    }
    Write-Host "Tauri release executable, sidecar, single-instance and exit cleanup passed." -ForegroundColor Green
}
finally {
    if (-not $appProcess.HasExited) {
        $null = $appProcess.CloseMainWindow()
        if (-not $appProcess.WaitForExit(5000)) {
            Stop-Process -Id $appProcess.Id -Force -ErrorAction SilentlyContinue
        }
    }
    Start-Sleep -Milliseconds 500
    $smokeProcesses = @(Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
            $_.CommandLine.IndexOf($dataDir, [StringComparison]::OrdinalIgnoreCase) -ge 0
    } | Select-Object -ExpandProperty ProcessId)
    $managedPids = @($sidecarPids) + $smokeProcesses | Select-Object -Unique
    foreach ($pidValue in $managedPids) {
        if (Get-Process -Id $pidValue -ErrorAction SilentlyContinue) {
            Stop-Process -Id $pidValue -Force -ErrorAction SilentlyContinue
        }
    }
    Remove-Item Env:AGENTHUB_DESKTOP_DATA_DIR -ErrorAction SilentlyContinue
    $resolvedData = [IO.Path]::GetFullPath($dataDir)
    if ($resolvedData.StartsWith($tempRoot, [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $resolvedData -Recurse -Force -ErrorAction SilentlyContinue
    }
}
