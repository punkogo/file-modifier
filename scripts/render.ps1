# Wrapper for render_sync.py — delegates all logic to Python CLI
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
python "$scriptDir\..\tools\render_sync.py" @Args
