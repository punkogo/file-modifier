#!/usr/bin/env bash
# render.sh - Thin wrapper that delegates to the Python CLI.
# Usage: ./scripts/render.sh <command> [options]
set -euo pipefail
python tools/render_sync.py "$@"
