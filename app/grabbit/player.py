"""Hands a stream to whatever plays videos on this PC.

Preferred players are launched directly, because they can start paused and be
given a window title. Anything else gets a one-line .m3u playlist opened with
the system default, which is what Windows uses for playlists.
"""

import logging
import os
import subprocess
import tempfile
import winreg

from .util import CREATE_NO_WINDOW, safe_filename

log = logging.getLogger(__name__)


def _from_registry(path: str, name: str = '') -> str | None:
    for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(root, path) as key:
                value = winreg.QueryValueEx(key, name)[0]
                if value:
                    return str(value)
        except OSError:
            continue
    return None


def _candidates():
    """(name, exe, extra args) for players that can start paused."""
    program_files = [os.environ.get('ProgramFiles', r'C:\Program Files'),
                     os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')]

    mpv = None
    for folder in program_files:
        for name in ('mpv\\mpv.exe', 'mpv.net\\mpvnet.exe'):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                mpv = candidate
    import shutil as _shutil
    mpv = mpv or _shutil.which('mpv')
    if mpv:
        yield 'mpv', mpv, ['--pause', '--keep-open=yes']

    vlc_dir = _from_registry(r'SOFTWARE\VideoLAN\VLC', 'InstallDir')
    vlc = os.path.join(vlc_dir, 'vlc.exe') if vlc_dir else None
    if not vlc or not os.path.isfile(vlc):
        for folder in program_files:
            candidate = os.path.join(folder, 'VideoLAN', 'VLC', 'vlc.exe')
            if os.path.isfile(candidate):
                vlc = candidate
                break
    if vlc and os.path.isfile(vlc):
        yield 'vlc', vlc, ['--start-paused', '--no-video-title-show']

    for folder in program_files:
        for name in ('DAUM\\PotPlayer\\PotPlayerMini64.exe', 'DAUM\\PotPlayer\\PotPlayer64.exe',
                     'MPC-HC\\mpc-hc64.exe', 'MPC-BE\\mpc-be64.exe', 'mpc-hc\\mpc-hc64.exe'):
            candidate = os.path.join(folder, name)
            if os.path.isfile(candidate):
                yield os.path.basename(candidate), candidate, []


def find_player() -> tuple[str, str, list] | None:
    for entry in _candidates():
        return entry
    return None


def play(url: str, title: str = '', start_paused: bool = True) -> tuple[bool, str]:
    """Open a URL or local file in a video player. Returns (ok, player name)."""
    found = find_player()
    if found:
        name, exe, flags = found
        args = [exe]
        if start_paused:
            args += flags
        if title and name == 'mpv':
            args.append(f'--force-media-title={title}')
        args.append(url)
        try:
            subprocess.Popen(args, creationflags=CREATE_NO_WINDOW)
            return True, name
        except OSError as exc:
            log.warning('could not start %s: %s', exe, exc)

    # No known player: let Windows decide via a tiny playlist file.
    try:
        folder = os.path.join(tempfile.gettempdir(), 'Grabbit')
        os.makedirs(folder, exist_ok=True)
        name = safe_filename(title or 'preview', 'preview', 60)
        path = os.path.join(folder, f'{name}.m3u')
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write('#EXTM3U\n')
            if title:
                handle.write(f'#EXTINF:-1,{title}\n')
            handle.write(url + '\n')
        os.startfile(path)  # noqa: S606 - opens the user's own player
        return True, 'default player'
    except OSError as exc:
        log.warning('could not open a player: %s', exc)
        return False, ''
