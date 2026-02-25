# render.ps1 - Thin wrapper that delegates to the Python CLI.
# Usage: .\scripts\render.ps1 <command> [options]
# Note: use 'py' instead of 'python' if 'python' is not in PATH on Windows.
python tools/render_sync.py @Args
