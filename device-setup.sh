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

echo ""
echo "Done. Reboot and Journal will launch automatically on TTY1."
