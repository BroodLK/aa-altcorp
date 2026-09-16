[CmdletBinding()]
param(
    [switch]$SkipPreCommit,
    [switch]$SkipTox
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$ruff = Join-Path $projectRoot ".venv\Scripts\ruff.exe"
$preCommit = Join-Path $projectRoot ".venv\Scripts\pre-commit.exe"
$tox = Join-Path $projectRoot ".venv\Scripts\tox.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "The project virtual environment was not found at $python. Create it and install .[dev] first."
}

function Invoke-Step {
    param([string]$Name, [scriptblock]$Command)
    Write-Host "`n=== $Name ===" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "$Name failed with exit code $LASTEXITCODE."
    }
}

if (-not $SkipPreCommit) {
    Invoke-Step "Pre-commit" { & $preCommit run --all-files }
}

Invoke-Step "Ruff lint" { & $ruff check . }
Invoke-Step "Ruff format" { & $ruff format --check . }
Invoke-Step "Pytest" { & $python -m pytest }

if (-not $SkipTox) {
    Invoke-Step "Alliance Auth tox tests" { & $tox -e py312 }
}

Write-Host "`nAlliance Auth plugin checks passed. The branch is ready to commit or push." -ForegroundColor Green
