<#
  Start Sentinel.

      .\run.ps1
      .\run.ps1 -Port 8888

  Creates a project-local .venv on first run and installs from requirements.txt.
  The point of the launcher is that the interpreter is chosen here, by absolute
  path, rather than by whatever "python" happens to mean in the current shell —
  that ambiguity is what produces "No module named uvicorn" on a machine where
  uvicorn is plainly installed.
#>
param(
    [int]$Port = 8777
)

Set-Location -Path $PSScriptRoot

function Fail($msg) {
    Write-Host ''
    Write-Host "Setup failed: $msg" -ForegroundColor Red
    Write-Host ''
    exit 1
}

$venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'

# Windows caps paths at 260 characters unless long-path support is enabled, and
# nested site-packages paths are long. A deep checkout fails inside ensurepip
# with a bare non-zero exit, which is near-impossible to diagnose from the error.
if ($PSScriptRoot.Length -gt 120) {
    Write-Host "Warning: this folder's path is $($PSScriptRoot.Length) characters." -ForegroundColor Yellow
    Write-Host 'Windows may fail to build the virtual environment. Prefer somewhere short, e.g. C:\dev\sentinel.' -ForegroundColor Yellow
    Write-Host ''
}

if (-not (Test-Path $venvPy)) {
    Write-Host 'No .venv found - creating one (first run only).' -ForegroundColor Cyan

    # Prefer the py launcher: it resolves a real CPython and never the Microsoft
    # Store stub that sits on PATH with no packages behind it.
    $bootstrap = if (Get-Command py -ErrorAction SilentlyContinue) { 'py' }
                 elseif (Get-Command python -ErrorAction SilentlyContinue) { 'python' }
                 else { Fail 'no Python found. Install 3.10+ from python.org and re-run.' }

    & $bootstrap -m venv .venv
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $venvPy)) {
        Fail "could not create .venv (exit $LASTEXITCODE). If the path above is long, try a shorter folder."
    }

    # No `pip install --upgrade pip` here. In a freshly created venv that step
    # can abort with "Cannot uninstall pip: no RECORD file", which previously
    # killed this script before any dependency was installed. The bundled pip is
    # sufficient to read requirements.txt.
    Write-Host 'Installing dependencies...' -ForegroundColor Cyan
    & $venvPy -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Fail "pip install failed (exit $LASTEXITCODE). See the output above." }
}

# Verify before launching, so a half-built environment fails with a clear
# message instead of a traceback from somewhere inside uvicorn.
& $venvPy -c "import fastapi, uvicorn, pandas, numpy, requests, websockets" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Dependencies missing or broken - reinstalling...' -ForegroundColor Yellow
    & $venvPy -m pip install -r requirements.txt --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { Fail 'could not install dependencies.' }
    & $venvPy -c "import fastapi, uvicorn, pandas, numpy, requests, websockets" 2>$null
    if ($LASTEXITCODE -ne 0) { Fail 'dependencies still not importable after reinstall.' }
}

Write-Host ''
Write-Host ("Interpreter : " + $venvPy) -ForegroundColor DarkGray
Write-Host ("Dashboard   : http://localhost:$Port") -ForegroundColor Green
Write-Host 'Stop        : Ctrl+C' -ForegroundColor DarkGray
Write-Host ''

& $venvPy -m uvicorn sentinel.server:app --port $Port
