# Packages Grabbit into dist\Grabbit\Grabbit.exe together with its tools.
#
#   powershell -ExecutionPolicy Bypass -File build\build_app.ps1
#
# Needs bootstrap.ps1 (venv + ffmpeg + deno) and build_aria2.sh (vendor\aria2c.exe).

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$Root  = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $env:LOCALAPPDATA 'GrabbitBuild'
$Venv  = Join-Path $Cache 'venv'
$Comp  = Join-Path $Cache 'components'
$Py    = Join-Path $Venv 'Scripts\python.exe'
$Dist  = Join-Path $Root 'dist\Grabbit'
$Tools = Join-Path $Dist 'tools'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $Py)) { Fail "venv missing - run build\bootstrap.ps1 first" }
if (-not (Test-Path (Join-Path $Root 'vendor\aria2c.exe'))) { Fail "vendor\aria2c.exe missing - run build\build_aria2.sh first" }

Write-Host '>> icon'
& $Py (Join-Path $PSScriptRoot 'make_icon.py')
if ($LASTEXITCODE -ne 0) { Fail 'icon generation failed' }

Write-Host '>> PyInstaller'
Push-Location $Root
& $Py -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root 'dist') `
    --workpath (Join-Path $Cache 'pyinstaller') (Join-Path $PSScriptRoot 'grabbit.spec')
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "PyInstaller failed ($code)" }
if (-not (Test-Path (Join-Path $Dist 'Grabbit.exe'))) { Fail 'Grabbit.exe was not produced' }

Write-Host '>> bundling tools'
New-Item -ItemType Directory -Force $Tools, (Join-Path $Tools 'ffmpeg'), (Join-Path $Tools 'deno') | Out-Null
Copy-Item (Join-Path $Root 'vendor\aria2c.exe') $Tools -Force
Copy-Item (Join-Path $Root 'vendor\aria2-COPYING.txt') (Join-Path $Tools 'aria2-COPYING.txt') -Force -ErrorAction SilentlyContinue
if (Test-Path (Join-Path $Comp 'ffmpeg')) {
    Copy-Item (Join-Path $Comp 'ffmpeg\*') (Join-Path $Tools 'ffmpeg') -Force -Recurse
} else { Write-Host 'WARNING: ffmpeg not found; video+audio merging will not work' -ForegroundColor Yellow }
if (Test-Path (Join-Path $Comp 'deno\deno.exe')) {
    Copy-Item (Join-Path $Comp 'deno\deno.exe') (Join-Path $Tools 'deno') -Force
} else { Write-Host 'WARNING: deno not found; some YouTube formats may be missing' -ForegroundColor Yellow }
if (Test-Path (Join-Path $Comp 'gallery-dl\gallery-dl.exe')) {
    Copy-Item (Join-Path $Comp 'gallery-dl\gallery-dl.exe') $Tools -Force
} else { Write-Host 'WARNING: gallery-dl not found; photo-first sites will be skipped' -ForegroundColor Yellow }

Copy-Item (Join-Path $Root 'README.md') $Dist -Force -ErrorAction SilentlyContinue

# Does it start? A build can come out of PyInstaller without complaint and
# still be unable to open a window - missing Qt's platform plugin once did
# exactly that. --self-test loads what a real start loads (app.py) and answers
# with the version; it is also what the updater asks of a new version before
# letting it replace an old one.
$Answer = Join-Path $env:TEMP "grabbit-build-self-test-$PID.txt"
Remove-Item $Answer -ErrorAction SilentlyContinue
$Probe = Start-Process -FilePath (Join-Path $Dist 'Grabbit.exe') -ArgumentList '--self-test', "`"$Answer`"" -PassThru
if (-not $Probe.WaitForExit(120000)) { $Probe.Kill(); Fail 'the built Grabbit.exe hung when started (--self-test)' }
$Said = if (Test-Path $Answer) { (Get-Content -Raw $Answer).Trim() } else { '' }
Remove-Item $Answer -ErrorAction SilentlyContinue
if (-not $Said) { Fail 'the built Grabbit.exe does not start (--self-test gave no answer)' }
Write-Host ">> it starts: Grabbit $Said"

# Which commit this is, so release.py can tell a build of the current code from
# an older one carrying the same version number. Changes not yet committed mark
# it as such: a release is only ever made from a commit anyone can check out.
$Stamp = Join-Path $Dist 'build-commit.txt'
$Commit = (& git -C $Root rev-parse HEAD 2>$null)
if ($Commit) {
    $Dirty = (& git -C $Root status --porcelain 2>$null)
    if ($Dirty) { $Commit = "$Commit-dirty" }
    Set-Content -Path $Stamp -Value $Commit -Encoding ascii
} else {
    Remove-Item $Stamp -ErrorAction SilentlyContinue
}

$size = (Get-ChildItem $Dist -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ('>> done: {0}  ({1:N0} MB)' -f (Join-Path $Dist 'Grabbit.exe'), ($size / 1MB))
