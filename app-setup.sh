#!/usr/bin/env bash
# app-setup.sh — Install system dependencies and set up Journal.
# Run once on a fresh device, then run device-setup.sh.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Journal App Setup ==="
echo ""

# System update
#   apt upgrade is allowed to fail without aborting: some third-party
#   packages (e.g. firmware-b43-installer) download firmware from
#   external sites in their post-install and break on a flaky network.
#   That must not take Journal's setup down with it.
echo "Updating system packages..."
sudo apt update
sudo apt upgrade -y || echo "WARNING: some packages failed to upgrade — continuing."

# System packages
#   micro ranger        — fallback editor + file manager
#   pandoc libreoffice  — export pipeline (.md -> .docx -> .pdf)
#   cups cups-client    — printing (lp)
#   git cage foot       — self-update + kiosk Wayland compositor + terminal
#   xwayland            — cage requires it at startup or it won't launch
#   fonts-noto-core     — Noto Sans Mono (see support/foot.ini)
#   wl-clipboard xclip  — clipboard (Wayland / X11)
#   aspell aspell-en    — spell check
#   network-manager     — Wi-Fi scan/connect from Options (nmcli)
#   python3 *           — runtime
echo "Installing required packages..."
sudo apt install -y \
    curl \
    micro ranger \
    pandoc libreoffice \
    cups cups-client \
    git cage foot xwayland \
    fonts-noto-core \
    wl-clipboard xclip \
    aspell aspell-en \
    network-manager \
    python3 python3-pip python3-venv \
    || echo "WARNING: some packages failed to install — continuing."

# Configure anything left half-installed (e.g. a firmware package whose
# external download failed), so it doesn't block later apt runs. The
# broken package stays broken but no longer aborts setup.
sudo dpkg --configure -a || true

# File Browser — optional web share of the vault, toggled from Journal's
# exports screen (press s). Single Go binary; the official script picks
# the right build for this CPU (incl. ARM).
if ! command -v filebrowser >/dev/null 2>&1; then
    echo "Installing File Browser..."
    curl -fsSL https://raw.githubusercontent.com/filebrowser/get/master/get.sh | bash
fi

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
# Sudo-free reboot (same approach as Journal's shutdown: relies on the
# logged-in console session's logind/polkit rights, not sudo).
shutdown -r now
