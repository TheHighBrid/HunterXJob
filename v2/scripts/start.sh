#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source .venv/bin/activate
mkdir -p logs run data

if [[ -f run/api.pid ]] && kill -0 "$(cat run/api.pid)" 2>/dev/null; then
  echo "HunterXJob API is already running with PID $(cat run/api.pid)."
else
  nohup python -m uvicorn app.main:app --host 127.0.0.1 --port 8011 > logs/api.log 2>&1 &
  echo $! > run/api.pid
fi

for _ in {1..30}; do
  if curl -fsS http://127.0.0.1:8011/api/health >/dev/null 2>&1; then
    echo "HunterXJob v2 is running at http://127.0.0.1:8011"
    exit 0
  fi
  sleep 1
done

echo "Startup failed. Check logs/api.log"
exit 1
