#!/usr/bin/env bash
# journal launcher — uses venv with prompt_toolkit.
#
# Usage:
#   ./run.sh                    # normal run
#   JOURNAL_VAULT=~/notes ./run.sh   # custom vault directory

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer venv if it exists
if [ -f "${SCRIPT_DIR}/.venv/bin/python3" ]; then
    exec "${SCRIPT_DIR}/.venv/bin/python3" "${SCRIPT_DIR}/journal.py" "$@"
fi

# Fall back to system python (prompt_toolkit must be installed)
exec python3 "${SCRIPT_DIR}/journal.py" "$@"
