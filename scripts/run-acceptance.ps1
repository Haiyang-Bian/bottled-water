param(
  [string]$ApiBaseUrl = $env:AGENTHUB_API_BASE_URL,
  [string]$WebBaseUrl = $env:AGENTHUB_E2E_BASE_URL
)

$ErrorActionPreference = "Stop"

if ($ApiBaseUrl) {
  uv run --project backend --package agenthub-backend --extra dev pytest backend/tests --live-base-url $ApiBaseUrl
} else {
  uv run --project backend --package agenthub-backend --extra dev pytest backend/tests
}

if (Test-Path "frontend/package.json") {
  Push-Location frontend
  try {
    corepack pnpm vitest run
  } finally {
    Pop-Location
  }
}

if ($WebBaseUrl) {
  Push-Location frontend
  try {
    corepack pnpm exec playwright test -c ..\e2e\playwright.config.ts
  } finally {
    Pop-Location
  }
}
