#!/usr/bin/env bash
set -u

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PASS=0
FAIL=0

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    printf '%-24s OK\n' "$name"
    PASS=$((PASS + 1))
  else
    printf '%-24s FAIL\n' "$name"
    FAIL=$((FAIL + 1))
  fi
}

check "Python 3.12" python3.12 --version
check "Virtual environment" test -x .venv/bin/python
check "Environment file" test -f .env
check "Writable data directory" bash -c 'mkdir -p data && touch data/.doctor && rm data/.doctor'
check "Disk free >= 5 GB" bash -c "[[ \$(df -Pk . | awk 'NR==2 {print \$4}') -ge 5242880 ]]"
check "Ollama API" curl -fsS http://127.0.0.1:11434/api/tags
check "Fast model installed" bash -c "curl -fsS http://127.0.0.1:11434/api/tags | grep -q 'llama3.2:1b'"
check "Application import" .venv/bin/python -c 'import app.main'
check "Pipeline tests" .venv/bin/python -m pytest -q tests/test_pipeline.py
check "API health" curl -fsS http://127.0.0.1:8011/api/health

printf '\nPassed: %s  Failed: %s\n' "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
