# Start Sentinel.
#
#   .\run.ps1
#
# Uses a project-local virtual environment in .venv, creating it and installing
# dependencies on first run. The point is that the interpreter is chosen by this
# script rather than by whatever "python" happens to mean in the current shell —
# that ambiguity is what produces "No module named uvicorn" on a machine where
# the package is plainly installed.

$ErrorActionPreference = 'Stop'
Set-Location -Path $PSScriptRoot

$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

if (-not (Test-Path $venvPy)) {
    Write-Host 'No .venv found — creating one (first run only).' -ForegroundColor Cyan

    # Prefer the py launcher: it finds a real CPython and never resolves to the
    # Microsoft Store stub that sits on PATH and has no packages.
    $bootstrap = $null
    if (Get-Command py -ErrorAction SilentlyContinue) { $bootstrap = 'py' }
    elseif (Get-Command python -ErrorAction SilentlyContinue) { $bootstrap = 'python' }
    else { throw 'No Python found. Install it from python.org and re-run.' }

    & $bootstrap -m venv .venv
    if (-not (Test-Path $venvPy)) { throw 'Failed to create .venv' }

    & $venvPy -m pip install --upgrade pip --quiet
    Write-Host 'Installing dependencies...' -ForegroundColor Cyan
    & $venvPy -m pip install -r requirements.txt
}

# Verify before launching, so a broken environment fails with a clear message
# instead of a traceback from inside uvicorn.
& $venvPy -c "import fastapi, uvicorn, pandas, numpy, requests, websockets" 2>$null
if (-not $?) {
    Write-Host 'Dependencies missing or broken — reinstalling...' -ForegroundColor Yellow
    & $venvPy -m pip install -r requirements.txt
}

Write-Host ''
Write-Host ('Interpreter : ' + $venvPy) -ForegroundColor DarkGray
Write-Host 'Dashboard   : http://localhost:8777' -ForegroundColor Green
Write-Host 'Stop        : Ctrl+C' -ForegroundColor DarkGray
Write-Host ''

& $venvPy -m uvicorn sentinel.server:app --port 8777
