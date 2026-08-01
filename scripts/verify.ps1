$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    $python = (Get-Command python -ErrorAction Stop).Source
}

Write-Host 'Running Python tests...'
& $python -m pytest

Write-Host 'Running JavaScript tests...'
& npm test

Write-Host 'Checking diff whitespace...'
& git diff --check
& git diff --cached --check

Write-Host 'Verification passed.'
