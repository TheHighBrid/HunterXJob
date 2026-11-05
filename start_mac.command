#!/usr/bin/env bash
# ==============================================================================
# HunterXJob v2 - Autonomous Live Application Launcher for macOS
# ==============================================================================
# Double-click in Finder or run from terminal: ./start_mac.command
# Runs in headed mode with real Google Chrome for bot & reCAPTCHA evasion.
# ==============================================================================

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# If launched from repo root, cd into v2
if [[ -d "${SCRIPT_DIR}/v2" ]]; then
    cd "${SCRIPT_DIR}/v2"
else
    cd "${SCRIPT_DIR}"
fi

clear
echo "=================================================================="
echo "   🚀 HunterXJob v2 - Autonomous Live Application Launcher        "
echo "=================================================================="
echo " Working directory : $(pwd)"
echo " Operating System  : macOS ($(uname -m))"
echo " Time              : $(date)"
echo " Mode              : Live Autonomous Submissions (Headed Chrome)"
echo "=================================================================="

# Check Python virtual environment
if [[ ! -f ".venv/bin/python" ]]; then
    echo "❌ Error: Virtual environment .venv/bin/python not found in $(pwd)"
    echo "Please run: python3 -m venv .venv && source .venv/bin/activate && pip install -e ."
    read -p "Press Enter to exit..."
    exit 1
fi

# Ensure Playwright Google Chrome is ready
if [[ ! -d "/Applications/Google Chrome.app" ]]; then
    echo "⚠️ Warning: Google Chrome not detected in /Applications/Google Chrome.app"
    echo "Chromium fallback will be used."
else
    echo "✓ Verified Google Chrome: /Applications/Google Chrome.app"
fi

# Create runtime directories
mkdir -p logs data run /tmp/hunterx_autonomous

# Export live autonomous settings
export ALLOW_LIVE_SUBMISSION=true
export APPLICATION_MODE=autonomous
export AUTOMATION_ENABLED=true
export PYTHONUNBUFFERED=1

echo "✓ Unlocked live autonomous submission permissions"
echo "✓ Streaming output in real time..."
echo "------------------------------------------------------------------"

# Run autonomous engine in headed mode
.venv/bin/python -u -m app.cli autonomous-run --live --headed --limit 10 --max-errors 10 "$@"

EXIT_CODE=$?
echo "------------------------------------------------------------------"
if [[ $EXIT_CODE -eq 0 ]]; then
    echo "✅ Autonomous cycle completed successfully."
else
    echo "⚠️ Autonomous cycle finished with exit code ${EXIT_CODE}."
fi
echo "=================================================================="
read -p "Press [Enter] to close this window..."
