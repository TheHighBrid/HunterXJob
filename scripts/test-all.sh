#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
MOBILE_DIR="$ROOT_DIR/mobile"
V2_DIR="$ROOT_DIR/v2"

command_exists() {
  command -v "$1" >/dev/null 2>&1
}

pyenv_python() {
  local version="$1"
  if command_exists pyenv && pyenv prefix "$version" >/dev/null 2>&1; then
    local prefix
    prefix="$(pyenv prefix "$version")"
    if [[ -x "$prefix/bin/python" ]]; then
      printf '%s\n' "$prefix/bin/python"
      return 0
    fi
  fi
  return 1
}

resolve_python() {
  local preferred="$1"
  local fallback="$2"

  if [[ -n "${PYTHON_BIN:-}" ]]; then
    printf '%s\n' "$PYTHON_BIN"
  elif command_exists "$preferred" && "$preferred" --version >/dev/null 2>&1; then
    printf '%s\n' "$preferred"
  elif pyenv_python "$fallback" >/dev/null 2>&1; then
    pyenv_python "$fallback"
  elif command_exists python3; then
    printf '%s\n' "python3"
  else
    echo "Unable to find a Python interpreter. Install $preferred or set PYTHON_BIN." >&2
    return 1
  fi
}

require_python_module() {
  local python_bin="$1"
  local module="$2"
  local install_hint="$3"
  if ! "$python_bin" -c "import ${module}" >/dev/null 2>&1; then
    cat >&2 <<MSG
Missing Python module '${module}' for interpreter: $python_bin
Install dependencies first:
  $install_hint
Or set PYTHON_BIN to a virtualenv/interpreter that already has them.
MSG
    return 1
  fi
}

BACKEND_PYTHON="$(resolve_python python3.11 3.11.15)"

if [[ -x "$BACKEND_DIR/.venv/bin/python" ]] && "$BACKEND_DIR/.venv/bin/python" -c 'import pytest' >/dev/null 2>&1; then
  BACKEND_TEST_PYTHON="$BACKEND_DIR/.venv/bin/python"
else
  BACKEND_TEST_PYTHON="$BACKEND_PYTHON"
fi

require_python_module "$BACKEND_TEST_PYTHON" pytest "cd backend && $BACKEND_PYTHON -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"
require_python_module "$BACKEND_TEST_PYTHON" sqlalchemy "cd backend && $BACKEND_PYTHON -m venv .venv && .venv/bin/pip install -r requirements-dev.txt"

echo "==> Backend syntax check ($BACKEND_PYTHON)"
(
  cd "$BACKEND_DIR"
  "$BACKEND_PYTHON" -m compileall -q app tests
)

echo "==> Backend pytest suite ($BACKEND_TEST_PYTHON)"
(
  cd "$BACKEND_DIR"
  "$BACKEND_TEST_PYTHON" -m pytest -q
)

if [[ -d "$V2_DIR" ]]; then
  V2_PYTHON="$(resolve_python python3.12 3.12.13)"
  if [[ -x "$V2_DIR/.venv/bin/python" ]] && "$V2_DIR/.venv/bin/python" -c 'import pytest' >/dev/null 2>&1; then
    V2_TEST_PYTHON="$V2_DIR/.venv/bin/python"
  else
    V2_TEST_PYTHON="$V2_PYTHON"
  fi

  require_python_module "$V2_TEST_PYTHON" pytest "cd v2 && $V2_PYTHON -m venv .venv && .venv/bin/pip install -e '.[test]'"
  require_python_module "$V2_TEST_PYTHON" pydantic "cd v2 && $V2_PYTHON -m venv .venv && .venv/bin/pip install -e '.[test]'"

  echo "==> V2 syntax check ($V2_PYTHON)"
  (
    cd "$V2_DIR"
    "$V2_PYTHON" -m compileall -q app tests
  )

  echo "==> V2 pytest suite ($V2_TEST_PYTHON)"
  (
    cd "$V2_DIR"
    "$V2_TEST_PYTHON" -m pytest -q
  )
fi

echo "==> Mobile TypeScript check"
(
  cd "$MOBILE_DIR"
  if [[ ! -d node_modules ]]; then
    echo "Missing mobile/node_modules. Run: cd mobile && npm install" >&2
    exit 1
  fi
  npm run typecheck
)
