#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="python3"
if [[ -x "$ROOT_DIR/.venv/bin/python3" ]]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python3"
fi

echo "==> Checking Python syntax..."
"$PYTHON_BIN" -m py_compile app.py strap/*.py tests/*.py

echo "==> Running unit tests..."
"$PYTHON_BIN" -m unittest discover -s tests -v "$@"
