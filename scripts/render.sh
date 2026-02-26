#!/usr/bin/env bash
# Wrapper for render_sync.py — delegates all logic to Python CLI

# Prefer python3, fall back to python, and fail if neither is available.
if command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  PYTHON=python
else
  echo "Error: Python 3 interpreter not found (tried 'python3' and 'python')." >&2
  exit 1
fi

"$PYTHON" "$(dirname "$0")/../tools/render_sync.py" "$@"
