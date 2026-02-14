#!/usr/bin/env bash
# Set up dependencies for Journal.
#
# Creates a virtual environment and installs prompt_toolkit + pygments.
# If pip is not available, prompt_toolkit can also be vendored manually.

set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "Setting up Journal..."

# Create venv if it doesn't exist
if [ ! -d "${SCRIPT_DIR}/.venv" ]; then
    echo "  Creating virtual environment..."
    python3 -m venv "${SCRIPT_DIR}/.venv"
fi

echo "  Installing dependencies..."
"${SCRIPT_DIR}/.venv/bin/pip" install --quiet prompt_toolkit pygments

echo "Done. Run with: ./run.sh"
