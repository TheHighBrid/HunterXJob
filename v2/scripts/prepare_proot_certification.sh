#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This preparation command is for the Ubuntu/Debian PRoot runtime (apt-get not found)." >&2
  exit 2
fi

run_root() {
  if [ "$(id -u)" -eq 0 ]; then
    "$@"
  elif command -v sudo >/dev/null 2>&1; then
    sudo "$@"
  else
    echo "Need package-manager privileges inside the Ubuntu PRoot to install Chromium dependencies." >&2
    exit 2
  fi
}

export DEBIAN_FRONTEND=noninteractive
run_root apt-get update
run_root apt-get install -y ca-certificates curl git python3.12 python3.12-venv python3-pip

if ! python3.12 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1; then
  echo "Python 3.12 is required for the certification runtime." >&2
  exit 2
fi

HUNTERX_PYTHON=python3.12 ./hunterx install
.venv/bin/pip install -e '.[browser,test]'
.venv/bin/python -m playwright install --with-deps chromium

.venv/bin/python - <<'PY'
from pathlib import Path

env_path = Path(".env")
lines = env_path.read_text(encoding="utf-8").splitlines()
updates = {
    "APPLICATION_MODE": "dry_run",
    "AUTOMATION_ENABLED": "false",
    "ALLOW_LIVE_SUBMISSION": "false",
    "CONTINUOUS_RUN_ENABLED": "false",
    "GREENHOUSE_BROWSER_VERIFY": "true",
    "GREENHOUSE_BROWSER_FALLBACK": "false",
}
keys = set(updates)
seen: set[str] = set()
out: list[str] = []
for line in lines:
    key = line.split("=", 1)[0] if "=" in line and not line.lstrip().startswith("#") else ""
    if key in updates:
        out.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        out.append(line)
for key in sorted(keys - seen):
    out.append(f"{key}={updates[key]}")

board_key = "GREENHOUSE_BOARD_TOKENS"
board_default = "d2l,geotab,faire,later,hootsuite"
for index, line in enumerate(out):
    if line.startswith(board_key + "="):
        if not line.split("=", 1)[1].strip():
            out[index] = f"{board_key}={board_default}"
        break
else:
    out.append(f"{board_key}={board_default}")

env_path.write_text("\n".join(out) + "\n", encoding="utf-8")
PY
chmod 600 .env

./hunterx doctor

echo
echo "Certification runtime is prepared in read-only dry-run mode."
echo "Next gate: load/verify the real profile, then run ./hunterx certify-greenhouse"
