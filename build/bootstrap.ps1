# Grabbit build bootstrap - fetches everything needed to build Grabbit.
#
#   powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1 [-Part all|msys2|python|components]
#
# Everything heavy lives in %LOCALAPPDATA%\GrabbitBuild so the project folder
# stays small. Delete that folder any time; this script recreates it.
#   msys64\      MSYS2 + MinGW-w64 GCC + aria2's libraries (compiles aria2c.exe)
#   venv\        Python venv with PySide6, PyInstaller, yt-dlp
#   components\  ffmpeg\ and deno\ binaries bundled into the app
# The script is idempotent: finished steps are skipped on re-run.

param(
    [ValidateSet('all', 'aria2src', 'msys2', 'python', 'components')]
    [string]$Part = 'all'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$Root  = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $env:LOCALAPPDATA 'GrabbitBuild'
$Dl    = Join-Path $Cache 'downloads'
$Msys  = Join-Path $Cache 'msys64'
$Venv  = Join-Path $Cache 'venv'
$Comp  = Join-Path $Cache 'components'
New-Item -ItemType Directory -Force $Dl, $Comp | Out-Null

$Aria2Version = 'release-1.37.0'
$DenoVersion = 'v2.9.7'
# gallery-dl publishes its Windows binaries from its builds repo, not from the
# tagged releases (which carry source only).
$GalleryDlBuild = '2026.09.18'
$MsysPackages = @(
    'patch'
    'mingw-w64-ucrt-x86_64-gcc'
    'mingw-w64-ucrt-x86_64-autotools'
    'mingw-w64-ucrt-x86_64-gettext-tools'
    'mingw-w64-ucrt-x86_64-pkgconf'
    'mingw-w64-ucrt-x86_64-c-ares'
    'mingw-w64-ucrt-x86_64-expat'
    'mingw-w64-ucrt-x86_64-sqlite3'
    'mingw-w64-ucrt-x86_64-zlib'
    'mingw-w64-ucrt-x86_64-gmp'
    'mingw-w64-ucrt-x86_64-libssh2'
)

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Get-File($url, $dest) {
    if (Test-Path $dest) { Write-Host "cached  $dest"; return }
    Write-Host "fetch   $url"
    & curl.exe -L --fail --retry 3 --silent --show-error -o "$dest.part" $url
    if ($LASTEXITCODE -ne 0) { Fail "download failed: $url" }
    Move-Item -Force "$dest.part" $dest
}

function Invoke-Msys([string]$cmd) {
    $env:MSYSTEM = 'UCRT64'
    $env:CHERE_INVOKING = '1'
    $env:MSYS2_PATH_TYPE = 'minimal'
    # Out-Host keeps bash output out of the function's return value.
    & (Join-Path $Msys 'usr\bin\bash.exe') -lc $cmd | Out-Host
    return $LASTEXITCODE
}

function Stop-MsysProcesses {
    Get-Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -and $_.Path.StartsWith($Msys, [StringComparison]::OrdinalIgnoreCase) } |
        Stop-Process -Force -ErrorAction SilentlyContinue
}

# --------------------------------------------------------------------------- aria2 source
# Not kept in git: it is upstream's code, fetched here so a fresh clone can
# build the same engine. build_aria2.sh then applies build/patches/*.patch.
if ($Part -in 'all', 'aria2src') {
    $aria2Dir = Join-Path $Root 'aria2'
    if (-not (Test-Path (Join-Path $aria2Dir 'configure.ac'))) {
        $tarball = Join-Path $Dl "aria2-$Aria2Version.tar.gz"
        Get-File "https://github.com/aria2/aria2/archive/refs/tags/$Aria2Version.tar.gz" $tarball
        Write-Host "extract aria2 -> $aria2Dir"
        New-Item -ItemType Directory -Force $aria2Dir | Out-Null
        & tar.exe -xf $tarball -C $aria2Dir --strip-components=1
        if (-not (Test-Path (Join-Path $aria2Dir 'configure.ac'))) { Fail 'aria2 source extraction failed' }
    }
    Write-Host 'aria2 source ready'
}

# --------------------------------------------------------------------------- MSYS2
if ($Part -in 'all', 'msys2') {
    $sfx = Join-Path $Dl 'msys2-base-x86_64-latest.sfx.exe'
    Get-File 'https://github.com/msys2/msys2-installer/releases/download/nightly-x86_64/msys2-base-x86_64-latest.sfx.exe' $sfx
    if (-not (Test-Path (Join-Path $Msys 'usr\bin\bash.exe'))) {
        Write-Host "extract MSYS2 -> $Msys"
        & $sfx -y "-o$Cache" | Out-Null
        if (-not (Test-Path (Join-Path $Msys 'usr\bin\bash.exe'))) { Fail 'MSYS2 extraction failed' }
    }
    $marker = Join-Path $Msys '.grabbit-packages'
    $want = ($MsysPackages -join ' ')
    if (-not (Test-Path $marker) -or (Get-Content $marker -Raw).Trim() -ne $want) {
        Write-Host 'init    MSYS2 (first run, keyring)'
        [void](Invoke-Msys 'true')
        foreach ($i in 1..2) {
            Write-Host "update  pacman -Syuu (pass $i)"
            [void](Invoke-Msys 'pacman -Syuu --noconfirm --overwrite "*"')
            Stop-MsysProcesses
        }
        Write-Host "install $want"
        $rc = Invoke-Msys "pacman -S --needed --noconfirm $want"
        if ($rc -ne 0) { Fail "pacman install failed ($rc)" }
        Set-Content -Path $marker -Value $want -Encoding ascii
    }
    Write-Host 'MSYS2 ready'
}

# --------------------------------------------------------------------------- Python
if ($Part -in 'all', 'python') {
    $py = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Host "venv    $Venv"
        & py -3.12 -m venv $Venv
        if ($LASTEXITCODE -ne 0) { Fail 'venv creation failed (needs Python 3.12 via the py launcher)' }
    }
    & $py -m pip install --disable-pip-version-check --upgrade pip | Out-Null
    & $py -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed' }
    Write-Host 'Python venv ready'
}

# --------------------------------------------------------------------------- ffmpeg + deno
if ($Part -in 'all', 'components') {
    $ffZip = Join-Path $Dl 'ffmpeg-master-latest-win64-gpl-shared.zip'
    Get-File 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip' $ffZip
    $ffDir = Join-Path $Comp 'ffmpeg'
    if (-not (Test-Path (Join-Path $ffDir 'ffmpeg.exe'))) {
        $tmp = Join-Path $Dl 'ffmpeg-extract'
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $tmp, $ffDir | Out-Null
        & tar.exe -xf $ffZip -C $tmp
        $exe = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
        if (-not $exe) { Fail 'ffmpeg.exe not found in archive' }
        Get-ChildItem $exe.DirectoryName | Where-Object { $_.Name -ne 'ffplay.exe' } | Copy-Item -Destination $ffDir
        $lic = Get-ChildItem $tmp -Recurse -Filter 'LICENSE*' | Select-Object -First 1
        if ($lic) { Copy-Item $lic.FullName (Join-Path $ffDir 'LICENSE.txt') }
        Remove-Item -Recurse -Force $tmp
    }
    # gallery-dl is run as a separate program, so the official standalone build
    # is used rather than the Python package (see app/grabbit/gallerydl.py).
    $galleryExe = Join-Path $Comp 'gallery-dl\gallery-dl.exe'
    if (-not (Test-Path $galleryExe)) {
        New-Item -ItemType Directory -Force (Split-Path $galleryExe) | Out-Null
        Get-File "https://github.com/gdl-org/builds/releases/download/$GalleryDlBuild/gallery-dl_windows.exe" $galleryExe
    }

    $denoZip = Join-Path $Dl "deno-$DenoVersion-x86_64-pc-windows-msvc.zip"
    Get-File "https://github.com/denoland/deno/releases/download/$DenoVersion/deno-x86_64-pc-windows-msvc.zip" $denoZip
    $denoDir = Join-Path $Comp 'deno'
    if (-not (Test-Path (Join-Path $denoDir 'deno.exe'))) {
        New-Item -ItemType Directory -Force $denoDir | Out-Null
        & tar.exe -xf $denoZip -C $denoDir
        if (-not (Test-Path (Join-Path $denoDir 'deno.exe'))) { Fail 'deno.exe not found in archive' }
    }
    Write-Host 'components ready'
}
