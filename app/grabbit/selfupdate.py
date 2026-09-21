"""Installing a newer Grabbit over this one, on Windows.

Grabbit runs from a folder - Grabbit.exe, _internal and tools - and Windows
will not let a running program's files be replaced. So the swap happens after
this copy has quit, done by a short PowerShell script that lives in neither
folder:

  1. download the new zip, checking its size and SHA-256 against the signed
     manifest (updates.py) before a byte of it is trusted;
  2. unpack it beside the install as <folder>.update, and check that the exe
     in it is the version promised and that it starts at all (--self-test);
  3. start the script and quit. It waits for this copy - and anything it left
     running - to let go, renames the install to <folder>.previous and the new
     one into its place, carries a portable copy's data across, and starts it;
  4. the new version says when its window is up. The script then deletes the
     old folder - or, if the new one never comes up, puts the old one back and
     starts that instead, which says so.

Nothing is replaced while it is running, and every step before the last can be
undone, so a failure anywhere leaves a Grabbit that works. Downloads that were
under way are restored and carry on, as after any restart.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from ctypes import wintypes
from pathlib import Path
from typing import Callable

from . import updates
from .updates import Asset, UpdateError

log = logging.getLogger(__name__)

# Downloads, the script and its log live here rather than in the data folder,
# because a portable copy's data folder moves during the swap.
WORK = Path(tempfile.gettempdir()) / 'grabbit-update'
EXE = 'Grabbit.exe'
# What a portable copy keeps beside the exe (paths.py), carried to the new one.
PORTABLE = ('portable.txt', 'data')

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def install_dir() -> Path | None:
    """The folder this copy runs from - or None when it runs from source."""
    if not getattr(sys, 'frozen', False):
        return None
    return Path(sys.executable).resolve().parent


def blocker(install: Path | None = None) -> str | None:
    """Why this copy cannot replace itself, if it cannot."""
    install = install or install_dir()
    if install is None:
        return 'This copy runs from source. Update it with git instead.'
    if sys.platform != 'win32':
        return 'Only the Windows app installs its own updates.'
    # os.access() reads only the read-only flag on Windows, so try for real.
    try:
        with tempfile.TemporaryFile(dir=install.parent):
            pass
    except OSError:
        return (f'Windows won’t let Grabbit change files in {install.parent}. '
                'Move the Grabbit folder somewhere of your own, like Documents, '
                'and it can update itself from there.')
    return None


def clean_environment() -> dict:
    """The environment for a program that must outlive this one.

    A frozen app hands its children variables describing its own unpacked
    files; another frozen app that inherits them tries to run from this one's,
    which are about to go. So they are dropped, and PyInstaller told to start
    afresh.
    """
    env = {k: v for k, v in os.environ.items()
           if not k.startswith('_PYI_') and k != '_MEIPASS2'}
    env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
    return env


# --------------------------------------------------------------- downloading

def download(asset: Asset, progress: Callable[[int, int], None] | None = None,
             cancel: threading.Event | None = None) -> Path:
    """Fetch the new zip and prove it is the one the manifest describes."""
    return updates.download(asset, WORK, progress, cancel)


# ------------------------------------------------------------------- staging

def staging_dir(install: Path) -> Path:
    return install.with_name(install.name + '.update')


def stage(archive: Path, version: str, install: Path,
          progress: Callable[[int, int], None] | None = None) -> Path:
    """Unpack the new version beside the install and make sure it is sound."""
    staged = staging_dir(install)
    shutil.rmtree(staged, ignore_errors=True)
    try:
        with zipfile.ZipFile(archive) as zf:
            members = [m for m in zf.infolist() if not m.is_dir()]
            needed = sum(m.file_size for m in members)
            free = shutil.disk_usage(install.parent).free
            if free < needed + (200 << 20):
                raise UpdateError(f'There isn’t enough free space next to Grabbit: the update '
                                  f'needs {needed / 1e6:.0f} MB and {free / 1e6:.0f} MB is free.')
            root = staged.resolve()
            for index, member in enumerate(members):
                # The zip holds one folder, Grabbit\, whose contents become the
                # new install. Anything that would land outside it is refused.
                parts = Path(member.filename).parts[1:]
                target = staged.joinpath(*parts).resolve() if parts else None
                if target is None or root not in target.parents:
                    raise UpdateError('The update contains a file it shouldn’t.')
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(member) as source, target.open('wb') as out:
                    shutil.copyfileobj(source, out, 1 << 20)
                if progress:
                    progress(index + 1, len(members))
    except (zipfile.BadZipFile, OSError) as error:
        shutil.rmtree(staged, ignore_errors=True)
        raise UpdateError(f'The update could not be unpacked: {error}') from None
    except UpdateError:
        shutil.rmtree(staged, ignore_errors=True)
        raise

    exe = staged / EXE
    found = exe_version(exe) if exe.exists() else ''
    if found != f'{version}.0':
        shutil.rmtree(staged, ignore_errors=True)
        raise UpdateError(f'The update says it is {version}, but the Grabbit.exe inside it is '
                          f'{found or "missing"}. It was not installed.')
    try:
        smoke_test(exe, version)
    except UpdateError:
        shutil.rmtree(staged, ignore_errors=True)
        raise
    return staged


def smoke_test(exe: Path, version: str) -> None:
    """Start the new exe far enough to load everything, without a window.

    It runs before the swap, while this copy is still in place - so a build
    that cannot start never replaces one that can.
    """
    WORK.mkdir(parents=True, exist_ok=True)
    answer = WORK / f'self-test-{version}.txt'
    answer.unlink(missing_ok=True)
    try:
        subprocess.run([str(exe), '--self-test', str(answer)], cwd=str(WORK),
                       env=clean_environment(), creationflags=CREATE_NO_WINDOW, timeout=120)
        said = answer.read_text(encoding='utf-8').strip() if answer.exists() else ''
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError(f'The new version would not start: {error}') from None
    finally:
        answer.unlink(missing_ok=True)
    if said != version:
        raise UpdateError('The new version would not start, so it was not installed.')


class _FixedFileInfo(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        'signature', 'struct_version', 'file_ms', 'file_ls', 'product_ms', 'product_ls',
        'flags_mask', 'flags', 'os', 'type', 'subtype', 'date_ms', 'date_ls')]


def exe_version(path: Path) -> str:
    """The version stamped into an exe (build/version_info.txt), as X.Y.Z.W."""
    try:
        api = ctypes.windll.version
    except AttributeError:
        return ''
    api.GetFileVersionInfoSizeW.argtypes = [wintypes.LPCWSTR, wintypes.LPDWORD]
    api.GetFileVersionInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    api.VerQueryValueW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.UINT)]
    size = api.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return ''
    buffer = ctypes.create_string_buffer(size)
    if not api.GetFileVersionInfoW(str(path), 0, size, buffer):
        return ''
    pointer, length = ctypes.c_void_p(), wintypes.UINT()
    if not api.VerQueryValueW(buffer, '\\', ctypes.byref(pointer), ctypes.byref(length)):
        return ''
    info = ctypes.cast(pointer, ctypes.POINTER(_FixedFileInfo)).contents
    return (f'{info.file_ms >> 16}.{info.file_ms & 0xFFFF}.'
            f'{info.file_ls >> 16}.{info.file_ls & 0xFFFF}')


# ----------------------------------------------------------------- swapping

def marker_path(version: str) -> Path:
    return WORK / f'started-{version}'


def log_path() -> Path:
    return WORK / 'install-update.log'


def launch_helper(install: Path, staged: Path, version: str) -> None:
    """Start the script that swaps the folders once this copy has quit."""
    WORK.mkdir(parents=True, exist_ok=True)
    script = WORK / 'install-update.ps1'
    script.write_text(HELPER_SCRIPT, encoding='utf-8-sig')      # PowerShell 5 wants the BOM
    marker = marker_path(version)
    marker.unlink(missing_ok=True)
    arguments = ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                 '-WindowStyle', 'Hidden', '-File', str(script),
                 '-OldPid', str(os.getpid()), '-Install', str(install), '-Staged', str(staged),
                 '-Version', version, '-Marker', str(marker), '-Log', str(log_path())]
    # Its working folder is WORK, not the install: a process sitting in a folder
    # stops that folder being renamed.
    subprocess.Popen(arguments, cwd=str(WORK), env=clean_environment(), close_fds=True,
                     creationflags=CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP)
    log.info('handed the install of %s to %s', version, script)


def record_started(marker: str) -> None:
    """Called by the new version once its window is up: the swap worked."""
    try:
        Path(marker).write_text('started', encoding='utf-8')
    except OSError:
        log.exception('could not record that the update started')


def cleanup(install: Path | None = None) -> None:
    """Remove what earlier updates left behind: the previous version's folder,
    a failed one, half-unpacked ones, and old downloads. Anything still in use
    is simply tried again next time."""
    install = install or install_dir()
    if install is not None:
        for leftover in ('.previous', '.failed', '.update'):
            shutil.rmtree(install.with_name(install.name + leftover), ignore_errors=True)
    if WORK.exists():
        for path in WORK.iterdir():
            if path.name == log_path().name:
                continue
            try:
                shutil.rmtree(path) if path.is_dir() else path.unlink()
            except OSError:
                pass


HELPER_SCRIPT = r'''
# Swaps a new Grabbit into place once the old one has quit. Written out and
# started by grabbit/selfupdate.py; see there for the whole sequence.
param(
    [int]$OldPid, [string]$Install, [string]$Staged, [string]$Version,
    [string]$Marker, [string]$Log
)
$ErrorActionPreference = 'Stop'
$Leaf = Split-Path -Leaf $Install
$Previous = "$Install.previous"
$Failed = "$Install.failed"
$Exe = Join-Path $Install 'Grabbit.exe'

function Say([string]$Message) {
    try { Add-Content -LiteralPath $Log -Value ('{0:yyyy-MM-dd HH:mm:ss} {1}' -f (Get-Date), $Message) } catch { }
}

# A virus scanner or the search indexer can hold a file for a moment after a
# program lets go of it, so anything that touches the folders gets a minute.
function Retry([scriptblock]$Action) {
    # Out-Null because a PowerShell function returns everything its commands
    # print, which would turn this $true into a list.
    for ($i = 0; $i -lt 120; $i++) {
        try { & $Action | Out-Null; return $true } catch { Start-Sleep -Milliseconds 500 }
    }
    Say "gave up: $($Error[0])"
    return $false
}

function Carry([string]$From, [string]$To) {
    foreach ($name in 'portable.txt', 'data') {
        $source = Join-Path $From $name
        if (Test-Path -LiteralPath $source) {
            Retry { Move-Item -LiteralPath $source -Destination (Join-Path $To $name) -Force } | Out-Null
        }
    }
}

function Start-Old {
    if (Test-Path -LiteralPath $Exe) {
        Start-Process -FilePath $Exe -ArgumentList '--update-failed', $Version -WorkingDirectory $Install
    }
}

Say "installing $Version into $Install"

# 1. The old copy quits once it has started this. Wait for it, then for
#    anything still running out of its folder - aria2, ffmpeg - to follow.
try { Wait-Process -Id $OldPid -Timeout 120 -ErrorAction SilentlyContinue } catch { }
if (Get-Process -Id $OldPid -ErrorAction SilentlyContinue) {
    Say 'the old copy never quit; nothing was changed'
    exit 1
}
$deadline = (Get-Date).AddSeconds(20)
do {
    # Reading a protected process's path can throw, so that is a "no".
    $left = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        try { $_.Path -and $_.Path.StartsWith("$Install\", 'OrdinalIgnoreCase') } catch { $false } })
    if ($left.Count -eq 0) { break }
    Start-Sleep -Milliseconds 500
} while ((Get-Date) -lt $deadline)
foreach ($process in $left) {
    Say "stopping $($process.Path), still running"
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
}

# 2. Swap the folders. Each rename is undone if the next one fails.
if (Test-Path -LiteralPath $Previous) { Retry { Remove-Item -LiteralPath $Previous -Recurse -Force } | Out-Null }
if (-not (Retry { Rename-Item -LiteralPath $Install -NewName "$Leaf.previous" })) {
    Say 'could not move the old version aside; nothing was changed'
    Start-Old
    exit 1
}
if (-not (Retry { Rename-Item -LiteralPath $Staged -NewName $Leaf })) {
    Say 'could not move the new version in; putting the old one back'
    Retry { Rename-Item -LiteralPath $Previous -NewName $Leaf } | Out-Null
    Start-Old
    exit 1
}
Carry $Previous $Install

# 3. Start the new version and wait for it to say its window is up. A new exe
#    is scanned the first time it runs, so it gets two minutes.
Remove-Item -LiteralPath $Marker -ErrorAction SilentlyContinue
$new = Start-Process -FilePath $Exe -WorkingDirectory $Install -PassThru `
           -ArgumentList '--updated', $Version, '--update-marker', "`"$Marker`""
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Date) -lt $deadline) {
    if (Test-Path -LiteralPath $Marker) {
        Retry { Remove-Item -LiteralPath $Previous -Recurse -Force } | Out-Null
        Say "updated to $Version"
        exit 0
    }
    if ($new.HasExited) { break }
    Start-Sleep -Milliseconds 500
}

# 4. It never came up: put the previous version back and start that.
Say "$Version did not start; putting the previous version back"
if (-not $new.HasExited) { Stop-Process -Id $new.Id -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 1
Carry $Install $Previous
if (Test-Path -LiteralPath $Failed) { Retry { Remove-Item -LiteralPath $Failed -Recurse -Force } | Out-Null }
Retry { Rename-Item -LiteralPath $Install -NewName "$Leaf.failed" } | Out-Null
Retry { Rename-Item -LiteralPath $Previous -NewName $Leaf } | Out-Null
Start-Old
exit 1
'''
