#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p data logs run

LOG_FILE="$ROOT/logs/prepare-certification-latest.log"
: > "$LOG_FILE"
exec > >(tee -a "$LOG_FILE") 2>&1

on_error() {
  local code=$?
  local line="${BASH_LINENO[0]:-unknown}"
  echo
  echo "Certification preparation failed (exit ${code}, line ${line})."
  echo "Persistent log: $LOG_FILE"
  exit "$code"
}
trap on_error ERR

echo "HunterXJob Greenhouse certification preparation"
echo "Runtime root: $ROOT"
echo "Log: $LOG_FILE"

# Termux also provides apt-get, so checking for apt-get alone does not prove
# that this process is inside the Ubuntu PRoot. Native Termux uses Android's
# bionic libc and its own Python/Rust toolchain, which cannot consume the Linux
# Python/Playwright environment used for certification. If this script is
# launched from Termux, cross into the existing Ubuntu PRoot automatically and
# continue there as PRoot root. Android root is never required.
is_native_termux() {
  local apt_path
  apt_path="$(command -v apt-get 2>/dev/null || true)"
  case "$apt_path" in
    /data/data/com.termux/files/usr/*) return 0 ;;
    *) return 1 ;;
  esac
}

if is_native_termux; then
  echo "Native Termux detected. Re-entering the certification setup inside Ubuntu PRoot."

  if ! command -v proot-distro >/dev/null 2>&1; then
    cat >&2 <<'EOF_TERMUX'
HunterXJob certification requires the Ubuntu PRoot runtime for Python 3.12 and Playwright Chromium.
The proot-distro command is not installed in Termux.
Install proot-distro and an Ubuntu distro, then rerun this same preparation command.
EOF_TERMUX
    exit 2
  fi

  # Confirm the expected distro exists before replacing this process. This is a
  # read-only probe and avoids turning a missing distro into a confusing nested
  # shell error.
  if ! proot-distro login ubuntu -- /bin/true >/dev/null 2>&1; then
    cat >&2 <<'EOF_UBUNTU'
The Ubuntu PRoot distro is not available under the alias "ubuntu".
HunterXJob will not install a new distro automatically because that can consume substantial storage.
Install or restore the Ubuntu PRoot distro, then rerun this same preparation command.
EOF_UBUNTU
    exit 2
  fi

  # proot-distro exposes the Termux home tree inside normal Linux guests, so
  # the repository remains reachable at the same absolute path. Pass the path
  # explicitly rather than assuming /root/HunterXJob.
  exec proot-distro login ubuntu -- \
    env HUNTERX_CERT_ROOT="$ROOT" \
    bash -lc 'cd "$HUNTERX_CERT_ROOT" && exec bash scripts/prepare_proot_certification.sh'
fi

apt_path="$(command -v apt-get 2>/dev/null || true)"
if [ -z "$apt_path" ] || [ "$apt_path" != "/usr/bin/apt-get" ]; then
  echo "This preparation command requires the Ubuntu/Debian PRoot runtime (/usr/bin/apt-get)." >&2
  exit 2
fi

# Surface resource pressure before Android kills the PRoot/XFCE process. The
# setup is allowed to continue above 1 GiB free, but warns below 3 GiB because
# Python wheels plus Playwright Chromium can temporarily consume substantial
# writable storage. Android root is never required.
available_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
available_mb=$((available_kb / 1024))
echo "Writable space available: ${available_mb} MiB"
if [ "$available_kb" -lt 1048576 ]; then
  echo "Less than 1 GiB is free on the filesystem containing HunterXJob." >&2
  echo "Stopping before Chromium/Python installation can destabilize the Android session." >&2
  exit 3
elif [ "$available_kb" -lt 3145728 ]; then
  echo "WARNING: less than 3 GiB is free. Setup will minimize caches and reuse existing installs."
fi

if [ -r /proc/meminfo ]; then
  mem_available_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
  if [ -n "${mem_available_kb:-}" ]; then
    mem_available_mb=$((mem_available_kb / 1024))
    echo "Memory available: ${mem_available_mb} MiB"
    if [ "$mem_available_kb" -lt 786432 ]; then
      echo "WARNING: less than 768 MiB RAM is currently available. Close heavy Android/XFCE apps before browser certification."
    fi
  fi
fi

export DEBIAN_FRONTEND=noninteractive
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export PIP_NO_CACHE_DIR=1
export UV_NO_CACHE=1

# Android itself does not need to be rooted. When the current PRoot session is
# uid 0, apt-get is running under PRoot's emulated root identity. When it is a
# regular PRoot user, leave OS packages untouched and use a user-space Python
# bootstrap instead.
if [ "$(id -u)" -eq 0 ]; then
  apt-get update
  apt-get install -y ca-certificates curl git python3.12 python3.12-venv python3-pip
else
  echo "Non-root Ubuntu/Debian PRoot session detected; skipping apt. Android root is not required."
fi

have_python_312() {
  command -v python3.12 >/dev/null 2>&1 && \
    python3.12 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}

ensure_uv() {
  if command -v uv >/dev/null 2>&1; then
    return 0
  fi

  # Prefer Astral's prebuilt installer. `pip install uv` on native Termux has no
  # compatible Android wheel and falls back to a large Rust source build, which
  # eventually fails at the Android linker with a missing -lgcc.
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  elif command -v wget >/dev/null 2>&1; then
    wget -qO- https://astral.sh/uv/install.sh | sh
  elif command -v python3 >/dev/null 2>&1 && python3 -m pip --version >/dev/null 2>&1; then
    python3 -m pip install --user --upgrade uv
  else
    echo "Need curl, wget, or Python+pip to bootstrap user-space Python 3.12." >&2
    exit 2
  fi

  export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
  if ! command -v uv >/dev/null 2>&1; then
    echo "uv bootstrap completed but the uv executable was not found." >&2
    exit 2
  fi
}

venv_is_312() {
  [ -x .venv/bin/python ] && \
    .venv/bin/python -c 'import sys; raise SystemExit(0 if sys.version_info[:2] == (3, 12) else 1)' >/dev/null 2>&1
}

# Resume a partially completed setup instead of deleting a valid environment on
# every retry. Only replace .venv when it is missing or uses the wrong Python.
if venv_is_312; then
  echo "Reusing existing Python 3.12 virtual environment."
else
  if [ -d .venv ]; then
    echo "Existing .venv is incomplete or not Python 3.12; rebuilding it once."
    rm -rf .venv
  fi

  if have_python_312; then
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
fi

if ! venv_is_312; then
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

chromium_path="$(.venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    print(p.chromium.executable_path)
PY
)"

if [ -x "$chromium_path" ]; then
  echo "Reusing existing Playwright Chromium: $chromium_path"
else
  echo "Installing Playwright Chromium without OS dependency elevation."
  .venv/bin/python -m playwright install chromium
fi

if ! .venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
    browser.close()
PY
then
  cat >&2 <<'EOF_CHROMIUM'
Chromium is installed but could not launch with the libraries currently present in this Ubuntu PRoot.
Android root is NOT required. The complete failure is preserved in logs/prepare-certification-latest.log.
Do not use Termux/Android sudo and do not root the device.
EOF_CHROMIUM
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
echo "Persistent setup log: $LOG_FILE"
echo "Next gate: load/verify the real profile, then run:"
echo "  .venv/bin/python scripts/greenhouse_certify.py"
