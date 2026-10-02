#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data logs run

if ! command -v apt-get >/dev/null 2>&1; then
  echo "This preparation command is for the Ubuntu/Debian PRoot runtime (apt-get not found)." >&2
  exit 2
fi

export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:$PATH"

# Android itself does not need to be rooted. When the current PRoot session is
# uid 0, apt-get is running under PRoot's emulated root identity. When it is a
# regular PRoot user, never call Android/Termux sudo: bootstrap the Python
# runtime in user space instead and leave OS packages untouched.
if [ "$(id -u)" -eq 0 ]; then
  apt-get update
  apt-get install -y ca-certificates curl git python3.12 python3.12-venv python3-pip
else
  echo "Non-root PRoot session detected; skipping apt/sudo. Android root is not required."
fi

have_python_312() {
  command -v python3.12 >/dev/null 2>&1 && \
    python3.12 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi

  if command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --user --upgrade uv
  elif command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  else
    echo "Need either Python+pip, curl, or wget to bootstrap user-space Python 3.12." >&2
    exit 2
  fi

  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv bootstrap completed but the uv executable was not found." >&2
    exit 2
  fi
}

rm -rf .venv

if have_python_312; then
  # python3.12-venv may be absent in a non-root PRoot session. Prefer stdlib
  # venv, then fall back to uv which does not require apt or sudo.
  if ! python3.12 -m venv .venv >/dev/null 2>&1; then
    ensure_uv
    uv venv --python "$(command -v python3.12)" --seed .venv
  fi
else
  echo "Python 3.12 is not installed system-wide; installing it in user space with uv."
  ensure_uv
  uv python install 3.12
  uv venv --python 3.12 --seed .venv
fi

if ! .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1; then
  echo "Python 3.12 is required for the certification runtime." >&2
  exit 2
fi

.venv/bin/python -m pip install --upgrade pip wheel setuptools
.venv/bin/pip install -e '.[browser,test]'

[ -f .env ] || cp .env.example .env
if ! grep -Eq '^API_KEY=.{32,}$' .env; then
  generated_key="$(.venv/bin/python -m app.cli api-key)"
  if grep -q '^API_KEY=' .env; then
    sed -i "s|^API_KEY=.*|API_KEY=${generated_key}|" .env
  else
    printf '\nAPI_KEY=%s\n' "$generated_key" >> .env
  fi
fi
chmod 600 .env

# `--with-deps` asks Playwright to elevate through sudo/apt on Linux. That is
# wrong for a non-root Android PRoot session and can hit Termux's real-root
# sudo wrapper. Download Chromium only; the browser preflight below verifies
# whether the existing Ubuntu userspace already has the required libraries.
.venv/bin/python -m playwright install chromium

if ! .venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    browser.close()
PY
then
  cat >&2 <<'EOF'
Chromium downloaded but could not launch with the libraries currently present in this Ubuntu PRoot.
Android root is NOT required. If OS libraries are missing, open the Ubuntu PRoot using its default
emulated root user, install the missing Ubuntu packages there, then rerun this script. Do not use
Termux/Android sudo or root the device.
EOF
  exit 2
fi

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

./hunterx migrate
./hunterx doctor

echo
echo "Certification runtime is prepared in read-only dry-run mode."
echo "Next gate: load/verify the real profile, then run:"
echo "  .venv/bin/python scripts/greenhouse_certify.py"
