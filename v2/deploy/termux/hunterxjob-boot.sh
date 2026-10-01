#!/data/data/com.termux/files/usr/bin/sh
# Termux:Boot start script for HunterXJob v2 (fallback when no Linux VM is used).
#
# Install with `./hunterx boot-install` (copies this to ~/.termux/boot/), or:
#   mkdir -p ~/.termux/boot && cp deploy/termux/hunterxjob-boot.sh ~/.termux/boot/hunterxjob
#   chmod 700 ~/.termux/boot/hunterxjob
# Requires the Termux:Boot app (same source as Termux, e.g. F-Droid), opened once.
# Android may still kill background apps: exempt Termux from battery optimisation.

HUNTERX_DIR="${HUNTERX_DIR:-$HOME/HunterXJob/v2}"

# Keep the CPU awake so scheduled cycles and backups actually run.
termux-wake-lock

cd "$HUNTERX_DIR" || exit 1
mkdir -p logs
# Give Wi-Fi/mobile data a moment after boot.
sleep 30
./hunterx start >> logs/boot.log 2>&1
