# From backend folder:  .\run.ps1
# Loads .env via uvicorn (python-dotenv already loaded in app) — ensure .env exists next to this script's cwd.
Set-Location $PSScriptRoot
$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Error "Missing .venv. Run: py -3.12 -m venv .venv  then  .\bootstrap.ps1"
    exit 1
}
& $py -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
