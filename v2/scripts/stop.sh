#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ -f run/api.pid ]]; then
  PID="$(cat run/api.pid)"
  if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    for _ in {1..20}; do
      kill -0 "$PID" 2>/dev/null || break
      sleep 0.25
    done
  fi
  rm -f run/api.pid
fi

echo "HunterXJob v2 stopped."
