#!/usr/bin/env bash
# Wrapper for render_sync.py — delegates all logic to Python CLI
python "$(dirname "$0")/../tools/render_sync.py" "$@"
