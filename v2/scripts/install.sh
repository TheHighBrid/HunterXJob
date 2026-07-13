#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run this inside Ubuntu proot where the shell user is root. No Android root is required."
  exit 1
fi

if ! command -v python3.12 >/dev/null 2>&1; then
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    python3.12 python3.12-venv python3-pip curl git ca-certificates
fi

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -e '.[browser,dev]'

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

mkdir -p data logs run
python -m playwright install chromium || {
  echo "Playwright Chromium installation failed. Core services are installed; browser doctor will report the missing dependency."
}

printf '\nHunterXJob v2 installed.\nRun: bash scripts/doctor.sh\nThen: bash scripts/start.sh\n'
