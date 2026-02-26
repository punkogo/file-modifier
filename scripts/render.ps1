# Wrapper for render_sync.py — delegates all logic to Python CLI
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = $env:PYTHON
if (-not $pythonExe) {
    $pythonExe = "python3"
}

# Validate that the selected Python interpreter exists; fall back to `python` if needed.
$pythonCmd = Get-Command $pythonExe -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    if (-not $env:PYTHON -and $pythonExe -eq "python3") {
        # Try `python` as a fallback when `python3` is not available and PYTHON is not set.
        $fallbackCmd = Get-Command "python" -ErrorAction SilentlyContinue
        if ($fallbackCmd) {
            $pythonExe = "python"
        } else {
            Write-Error "No suitable Python interpreter found. Please install Python 3 or ensure 'python3' or 'python' is on your PATH, or set the PYTHON environment variable."
            exit 1
        }
    } else {
        Write-Error "Python interpreter '$pythonExe' not found. Please check that the PYTHON environment variable is set correctly and that the interpreter is on your PATH."
        exit 1
    }
}
# Validate that the selected interpreter is Python 3.
$majorVersion = & $pythonExe -c "import sys; print(sys.version_info[0])" 2>$null
if ($LASTEXITCODE -ne 0 -or $majorVersion.Trim() -ne "3") {
    Write-Error "The selected Python interpreter ('$pythonExe') is not Python 3. Please install Python 3 and ensure 'python3' or 'python' points to it, or set the PYTHON environment variable to a Python 3 executable."
    exit 1
}
& $pythonExe "$scriptDir\..\tools\render_sync.py" @Args
