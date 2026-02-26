#!/usr/bin/env bash
# Wrapper for render_sync.py — delegates all logic to Python CLI

# Honour PYTHON env override, then prefer python3, then fall back to python.
if [ -n "$PYTHON" ]; then
  : # Use the caller-supplied interpreter as-is
elif command -v python3 >/dev/null 2>&1; then
  PYTHON=python3
elif command -v python >/dev/null 2>&1; then
  if python -c "import sys; sys.exit(0 if sys.version_info[0] == 3 else 1)" >/dev/null 2>&1; then
    PYTHON=python
  else
    echo "Error: 'python' is not Python 3. Please install Python 3 or set the PYTHON environment variable." >&2
    exit 1
  fi
else
  echo "Error: Python 3 interpreter not found (tried 'python3' and 'python')." >&2
  exit 1
fi

"$PYTHON" "$(dirname "$0")/../tools/render_sync.py" "$@"
