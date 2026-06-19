#!/usr/bin/env bash
# Convenience launcher: activates the venv and runs the dictation tool.
# Pass any dictate_bug.py flags through, e.g.:  ./dictate.sh --raw
set -euo pipefail
cd "$(dirname "$0")"
exec ./venv/bin/python dictate_bug.py "$@"
