# Publishes a Grabbit release, which both apps then offer as an update.
#
# Right-click it and choose Run with PowerShell: it asks for the version -
# offering the next one - and the release notes, then builds, checks and
# publishes, and waits for Enter at the end so whatever happened can be read
# before the window closes.
#
# Or from a terminal, with the choices given up front:
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --dry-run
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --version 1.3.0 --notes "What's new"
#
# All the work is in release.py - see the top of it for what each step does.
# This only runs it with the build venv's Python, which has what it needs.

# Nothing after it means it was started from Explorer, in a window of its own.
$asking = $args.Count -eq 0

$Py = Join-Path $env:LOCALAPPDATA 'GrabbitBuild\venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host 'ERROR: the build venv is missing - run build\bootstrap.ps1 first' -ForegroundColor Red
    $code = 1
} elseif ($asking) {
    & $Py (Join-Path $PSScriptRoot 'release.py') --ask
    $code = $LASTEXITCODE
} else {
    & $Py (Join-Path $PSScriptRoot 'release.py') @args
    $code = $LASTEXITCODE
}

if ($asking) {
    Write-Host ''
    Read-Host 'Press Enter to close' | Out-Null
}
exit $code
