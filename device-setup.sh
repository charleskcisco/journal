#!/usr/bin/env bash
# device-setup.sh — Configure the writerdeck to launch Journal on boot.
# Run this after app-setup.sh has completed and the device has rebooted.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Journal Device Setup ==="
echo ""

# Append auto-launch block to ~/.bashrc (idempotent)
BASHRC_MARKER="# Auto-launch on TTY1 (physical console)"
if grep -qF "$BASHRC_MARKER" ~/.bashrc; then
    echo "  ~/.bashrc: already configured, skipping."
else
    echo "  Configuring ~/.bashrc auto-launch..."
    echo "" >> ~/.bashrc
    cat "${SCRIPT_DIR}/support/bashrc" >> ~/.bashrc
fi

# Install start-deck.sh with the correct repo path substituted in
echo "  Installing ~/start-deck.sh..."
sed "s|/path/to/journal|${SCRIPT_DIR}|g" \
    "${SCRIPT_DIR}/support/start-deck.sh" > ~/start-deck.sh
chmod +x ~/start-deck.sh

# Install foot terminal config
echo "  Installing foot terminal config..."
mkdir -p ~/.config/foot
cp "${SCRIPT_DIR}/support/foot.ini" ~/.config/foot/foot.ini

# ── Boot tuning (optional, idempotent, needs sudo) ───────────────────
# A writerdeck doesn't need the network up before it starts writing.
# These two changes cut ~20s off boot-to-Journal on a Pi and no-op
# cleanly on images that lack the relevant bits.
echo "  Tuning boot for a faster start to Journal..."

# 1. cloud-init: provisioning overhead a deck never uses. Disable via the
#    reversible flag, NOT a purge -- a purge can strand a deck whose Wi-Fi
#    config is cloud-init/netplan-managed.
if [ -d /etc/cloud ] && [ ! -e /etc/cloud/cloud-init.disabled ]; then
    echo "    - disabling cloud-init (undo: sudo rm /etc/cloud/cloud-init.disabled)"
    sudo touch /etc/cloud/cloud-init.disabled || echo "      (skipped: needs sudo)"
fi

# 2. Stop logins -- and the tty1 autologin that launches Journal -- from
#    waiting on network.target. Journal then starts ~15s sooner; Wi-Fi and
#    Syncthing come up in the background. A full-unit override in /etc is
#    required because After= can't be reset from a drop-in.
_us_vendor=/usr/lib/systemd/system/systemd-user-sessions.service
_us_local=/etc/systemd/system/systemd-user-sessions.service
if [ ! -e "$_us_local" ] && grep -qE '^After=.*network\.target' "$_us_vendor" 2>/dev/null; then
    echo "    - removing the network.target wait from systemd-user-sessions"
    if sudo cp "$_us_vendor" "$_us_local"; then
        sudo sed -i '/^After=/ s/ *network\.target//' "$_us_local"
        sudo systemctl daemon-reload || true
    else
        echo "      (skipped: needs sudo)"
    fi
fi

echo ""
echo "Done. Reboot and Journal will launch automatically on TTY1."
