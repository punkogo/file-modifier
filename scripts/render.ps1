# Wrapper for render_sync.py — delegates all logic to Python CLI
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = $env:PYTHON
if (-not $pythonExe) {
    $pythonExe = "python3"
}
& $pythonExe "$scriptDir\..\tools\render_sync.py" @Args
