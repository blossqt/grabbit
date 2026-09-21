# Publishes a Grabbit release, which both apps then offer as an update.
#
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --dry-run
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --version 1.3.0 --notes "What's new"
#
# All the work is in release.py - see the top of it for what each step does.
# This only runs it with the build venv's Python, which has what it needs.

$Py = Join-Path $env:LOCALAPPDATA 'GrabbitBuild\venv\Scripts\python.exe'
if (-not (Test-Path $Py)) {
    Write-Host 'ERROR: the build venv is missing - run build\bootstrap.ps1 first' -ForegroundColor Red
    exit 1
}
& $Py (Join-Path $PSScriptRoot 'release.py') @args
exit $LASTEXITCODE
