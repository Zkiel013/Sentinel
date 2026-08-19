#!/usr/bin/env bash
# Start Sentinel on macOS or Linux.
#
#     ./run.sh
#     PORT=8888 ./run.sh
#
# Same contract as run.ps1: the interpreter is chosen here by path, not by
# whatever "python" resolves to in the calling shell.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${PORT:-8777}"
VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
  echo "No .venv found - creating one (first run only)."
  PY=""
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || { echo "Setup failed: no Python found. Install 3.10+." >&2; exit 1; }
  "$PY" -m venv .venv || { echo "Setup failed: could not create .venv." >&2; exit 1; }
  echo "Installing dependencies..."
  # deliberately no pip self-upgrade; the bundled pip can read requirements.txt
  "$VENV_PY" -m pip install -r requirements.txt --disable-pip-version-check
fi

if ! "$VENV_PY" -c "import fastapi, uvicorn, pandas, numpy, requests, websockets" 2>/dev/null; then
  echo "Dependencies missing or broken - reinstalling..."
  "$VENV_PY" -m pip install -r requirements.txt --disable-pip-version-check
fi

echo
echo "Interpreter : $VENV_PY"
echo "Dashboard   : http://localhost:$PORT"
echo "Stop        : Ctrl+C"
echo
exec "$VENV_PY" -m uvicorn sentinel.server:app --port "$PORT"
