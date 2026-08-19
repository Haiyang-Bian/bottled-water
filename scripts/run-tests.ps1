[CmdletBinding()]
param(
    [ValidateSet("backend", "frontend", "e2e")]
    [string]$Stack,

    [ValidateSet("auth", "security", "providers", "agents", "runtime", "models", "chat", "workflow", "desktop", "collaboration", "worktrees")]
    [string]$Module,

    [ValidateSet("unit", "integration", "component", "live")]
    [string]$Type,

    [switch]$All,
    [switch]$List
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$manifestPath = Join-Path $PSScriptRoot "test-groups.json"
$groups = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json -AsHashtable

if ($List) {
    foreach ($stackName in $groups.Keys | Sort-Object) {
        foreach ($groupName in $groups[$stackName].Keys | Sort-Object) {
            $count = @($groups[$stackName][$groupName]).Count
            Write-Output "$stackName $groupName ($count test target(s))"
        }
    }
    exit 0
}

if ($All) {
    if (-not $Stack) {
        throw "-All requires an explicit -Stack to prevent accidental repository-wide test runs."
    }
    $targets = @()
} else {
    if (-not $Stack -or -not $Module -or -not $Type) {
        throw "Specify -Stack, -Module, and -Type, or use -List. Full runs require -All -Stack <name>."
    }
    $groupName = "$Module`:$Type"
    if (-not $groups[$Stack].ContainsKey($groupName)) {
        throw "Unknown test group: $Stack $groupName. Use -List to view available groups."
    }
    $targets = @($groups[$Stack][$groupName])
    if ($targets.Count -eq 0) {
        throw "Test group is intentionally empty: $Stack $groupName. Add explicit targets before running it."
    }
}

switch ($Stack) {
    "backend" {
        Push-Location (Join-Path $repoRoot "backend")
        try {
            & uv run pytest -q @targets
        } finally {
            Pop-Location
        }
    }
    "frontend" {
        Push-Location (Join-Path $repoRoot "frontend")
        try {
            & pnpm exec vitest run --config tests/vitest.config.ts @targets
        } finally {
            Pop-Location
        }
    }
    "e2e" {
        Push-Location (Join-Path $repoRoot "e2e")
        try {
            & pnpm exec playwright test @targets
        } finally {
            Pop-Location
        }
    }
}

exit $LASTEXITCODE
