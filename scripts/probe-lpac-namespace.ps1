# P1 experiment orchestrator. Only the fixed ACL helper is elevated; tools are not.
[CmdletBinding()]
param(
    [ValidateSet('legacy', 'quiescent', 'completion')]
    [string]$Probe = 'legacy'
)

$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$python = Join-Path $repo '.venv\Scripts\python.exe'
$principal = [Security.Principal.WindowsPrincipal]::new(
    [Security.Principal.WindowsIdentity]::GetCurrent())
if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this orchestrator as the ordinary user, not as Administrator.'
}
$experiment = [guid]::NewGuid().ToString('N')
$report = Join-Path $repo "var\l4a-namespace-$experiment.json"
$stopFile = [IO.Path]::ChangeExtension($report, '.stop')
$output = Join-Path $repo "var\l4a-lpac-namespace-$experiment"
if ($Probe -eq 'quiescent') {
    $output = Join-Path $repo "var\l4a-quiescent-namespace-$experiment"
}
$helperExtra = @()
if ($Probe -eq 'completion') {
    $output = Join-Path $repo "var\l4a-completion-$experiment"
    $fixture = Join-Path $repo "var\l4a-symbolic-$experiment"
    if (Test-Path -LiteralPath $fixture) { throw 'Symbolic fixture already exists' }
    $private = Join-Path $fixture 'Private'
    [void][IO.Directory]::CreateDirectory($private)
    [IO.File]::WriteAllText((Join-Path $private 'sample.txt'), 'C')
    $helperExtra = @('--symbolic-fixture')
    Write-Output "Additional fixed action: create $fixture\link-to-private -> $private for the alias test."
    Write-Output 'No Developer Mode or system privilege setting is changed.'
}
$helper = Join-Path $PSScriptRoot 'probe-lpac-namespace-admin.py'
Write-Output "Namespace experiment: $experiment"
Write-Output 'The UAC helper can only add/remove five fixed query ACEs for this experiment.'
Write-Output 'Targets: \GLOBAL??, \GLOBAL??\C:, \GLOBAL??\D:, \GLOBAL??\MountPointManager, \\.\MountPointManager'
Write-Output "Report: $report"
$adminProcess = $null
$probeExit = 1
try {
    $helperArguments = @('-I', ('"{0}"' -f $helper), '--experiment', $experiment, '--apply') + $helperExtra
    $adminProcess = Start-Process -FilePath $python -ArgumentList $helperArguments `
        -Verb RunAs -WindowStyle Hidden -PassThru
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        if (Test-Path -LiteralPath $report) {
            try {
                $state = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
                if ($state.status -eq 'ready') { $ready = $true; break }
                if ($state.status -in @('failed', 'repair_required', 'cleaned')) {
                    throw "Initialization stopped: $($state | ConvertTo-Json -Depth 8 -Compress)"
                }
            } catch [System.ArgumentException] {
                # The bounded helper is flushing an intermediate report; retry reading.
            }
        }
        if ($adminProcess.HasExited) { break }
        Start-Sleep -Milliseconds 200
    }
    if (-not $ready) { throw "Namespace initialization was not ready; inspect $report" }
    if ($Probe -eq 'completion') {
        & $python (Join-Path $PSScriptRoot 'probe-lpac-completion.py') --output $output `
            --namespace-experiment $experiment
    } elseif ($Probe -eq 'quiescent') {
        & $python (Join-Path $PSScriptRoot 'probe-lpac-quiescent.py') --output $output `
            --toolchain --namespace-experiment $experiment
    } else {
        & $python (Join-Path $PSScriptRoot 'probe-windows-lpac.py') --output $output `
            --toolchain --registry-read --full-toolchain --instrumentation `
            --namespace-experiment $experiment
    }
    $probeExit = $LASTEXITCODE
} finally {
    # This marker requests removal only. It carries no executable or ACL parameters.
    if ($null -ne $adminProcess) {
        [IO.File]::WriteAllText($stopFile, 'remove this experiment capability')
        if (-not $adminProcess.WaitForExit(15000)) {
            Write-Warning "Cleanup is unconfirmed; inspect $report before another experiment."
        }
    }
}
if (Test-Path -LiteralPath $report) {
    $finalState = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
    Write-Output ($finalState | ConvertTo-Json -Depth 8)
    if ($finalState.status -ne 'cleaned') { exit 1 }
}
if ($Probe -eq 'completion' -and $probeExit -eq 0) {
    & $python (Join-Path $PSScriptRoot 'finalize-lpac-completion.py') --experiment $experiment
    $probeExit = $LASTEXITCODE
}
exit $probeExit
