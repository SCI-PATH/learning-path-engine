# Run from backend folder:  powershell -ExecutionPolicy Bypass -File .\bootstrap.ps1
# Recreates .venv with Python 3.12 and installs requirements (no global Python 3.14).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "Using Python launcher for 3.12..."
py -3.12 --version

if (Test-Path .venv) {
    Write-Host "Removing existing .venv ..."
    Remove-Item -Recurse -Force .venv
}

Write-Host "Creating .venv with Python 3.12..."
py -3.12 -m venv .venv

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) { throw "venv python not found at $py" }

& $py -m pip install --upgrade pip
& $py -m pip install -r requirements.txt

Write-Host ""
Write-Host "OK. Next commands:"
Write-Host '  .\.venv\Scripts\Activate.ps1'
Write-Host '  python scripts\ingest.py'
Write-Host '  uvicorn app.main:app --reload --port 8000'
Write-Host ""
Write-Host "Tip: always use  .\.venv\Scripts\python.exe -m pip  ...  so you never hit Python 3.14 by accident."
