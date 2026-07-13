#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
MOBILE_DIR="$ROOT_DIR/mobile"
if [[ -n "${PYTHON_BIN:-}" ]]; then
  PYTHON_BIN="$PYTHON_BIN"
elif python3.11 --version >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
else
  PYTHON_BIN="python3"
fi

echo "==> Backend syntax check"
(
  cd "$BACKEND_DIR"
  "$PYTHON_BIN" -m compileall -q app tests
)

echo "==> Backend pytest suite"
(
  cd "$BACKEND_DIR"
  TEST_PYTHON="$PYTHON_BIN"
  if [[ -x .venv/bin/python ]] && .venv/bin/python -c 'import pytest' >/dev/null 2>&1; then
    TEST_PYTHON=".venv/bin/python"
  fi
  "$TEST_PYTHON" -m pytest -q
)

echo "==> Mobile TypeScript check"
(
  cd "$MOBILE_DIR"
  npm run typecheck
)
