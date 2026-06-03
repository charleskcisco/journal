#!/usr/bin/env bash
# app-setup.sh — Install system dependencies and set up Journal.
# Run once on a fresh device, then run device-setup.sh.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Journal App Setup ==="
echo ""

# System update
echo "Updating system packages..."
sudo apt update
sudo apt upgrade -y

# System packages
#   micro ranger        — fallback editor + file manager
#   pandoc libreoffice  — export pipeline (.md -> .docx -> .pdf)
#   cups cups-client    — printing (lp)
#   git cage foot       — self-update + kiosk Wayland compositor + terminal
#   fonts-noto-core     — Noto Sans Mono (see support/foot.ini)
#   wl-clipboard xclip  — clipboard (Wayland / X11)
#   aspell aspell-en    — spell check
#   python3 *           — runtime
echo "Installing required packages..."
sudo apt install -y \
    micro ranger \
    pandoc libreoffice \
    cups cups-client \
    git cage foot \
    fonts-noto-core \
    wl-clipboard xclip \
    aspell aspell-en \
    python3 python3-pip python3-venv

# Python venv + Journal dependencies (delegated to setup.sh, which the
# self-update loop in run.sh also reuses).
echo "Setting up Python environment..."
"${SCRIPT_DIR}/setup.sh"

echo ""
echo "All done. Run Journal with: ./run.sh"
echo ""
echo "Next: ./device-setup.sh to launch Journal automatically on boot."
echo ""
echo "Rebooting in 5 seconds to apply updates (Ctrl+C to cancel)..."
sleep 5
sudo reboot now
