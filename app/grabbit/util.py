"""Small helpers shared by the engine and the UI."""

import ctypes
import os
import re
import subprocess
import sys
import time
from urllib.parse import urlparse

# Everything below the formatting helpers is shared with the Android build,
# where ctypes.wintypes does not exist - the Windows pieces stay lazy.
IS_WINDOWS = os.name == 'nt'

# --------------------------------------------------------------------------- formatting

_UNITS = ('B', 'KiB', 'MiB', 'GiB', 'TiB', 'PiB')


def human_size(num: float | None, empty: str = '') -> str:
    if num is None or num < 0:
        return empty
    value = float(num)
    for unit in _UNITS:
        if value < 1024 or unit == _UNITS[-1]:
            if unit == 'B':
                return f'{int(value)} B'
            return f'{value:.2f} {unit}' if value < 10 else f'{value:.1f} {unit}'
        value /= 1024
    return f'{value:.1f} {_UNITS[-1]}'


def human_speed(bps: float | None) -> str:
    if not bps:
        return ''
    return human_size(bps) + '/s'


def human_eta(seconds: float | None) -> str:
    if seconds is None or seconds < 0 or seconds > 100 * 86400:
        return '∞'
    seconds = int(seconds)
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f'{days}d {hours}h'
    if hours:
        return f'{hours}h {minutes}m'
    if minutes:
        return f'{minutes}m {secs}s'
    return f'{secs}s'


def human_duration(seconds: float | None) -> str:
    if not seconds:
        return ''
    seconds = int(round(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'


def human_time(ts: float | None) -> str:
    if not ts:
        return ''
    return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))


# --------------------------------------------------------------------------- links

_LINK_RE = re.compile(
    r'(magnet:\?[^\s"\'<>]+|(?:https?|ftp|sftp)://[^\s"\'<>]+)', re.IGNORECASE)
_TRAILING = '.,;:!?)]}。，'


def extract_links(text: str) -> list[str]:
    """Pull every http(s)/ftp/sftp/magnet link out of free text, deduplicated."""
    seen, links = set(), []
    for match in _LINK_RE.finditer(text or ''):
        link = match.group(1)
        while link and link[-1] in _TRAILING:
            # Keep a closing bracket that belongs to the URL, e.g. wiki links.
            if link[-1] == ')' and link.count('(') >= link.count(')'):
                break
            link = link[:-1]
        if link and link not in seen:
            seen.add(link)
            links.append(link)
    return links


_SITES = (
    ('YouTube', ('youtube.com', 'youtu.be', 'youtube-nocookie.com')),
    ('TikTok', ('tiktok.com', 'tiktokv.com')),
    ('Instagram', ('instagram.com',)),
    ('X', ('twitter.com', 'x.com')),
    ('Reddit', ('reddit.com', 'redd.it')),
    ('Facebook', ('facebook.com', 'fb.watch')),
    ('Vimeo', ('vimeo.com',)),
    ('Twitch', ('twitch.tv',)),
    ('SoundCloud', ('soundcloud.com',)),
    ('Bilibili', ('bilibili.com', 'b23.tv')),
    ('Dailymotion', ('dailymotion.com', 'dai.ly')),
    ('Pinterest', ('pinterest.com', 'pin.it')),
)


def site_name(url: str) -> str:
    """Friendly site name for a URL ('' when unknown)."""
    host = (urlparse(url).hostname or '').lower()
    for name, domains in _SITES:
        if any(host == d or host.endswith('.' + d) for d in domains):
            return name
    return ''


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or '').lower()
    return host[4:] if host.startswith('www.') else host


_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}


def safe_filename(name: str, fallback: str = 'download', max_len: int = 180) -> str:
    name = _BAD_CHARS.sub('_', name or '').strip().strip('.')
    name = re.sub(r'\s+', ' ', name)
    if not name:
        name = fallback
    stem, dot, ext = name.rpartition('.')
    if dot and len(ext) <= 8 and stem:
        stem = stem[:max_len - len(ext) - 1].rstrip(' .')
        name = f'{stem}.{ext}'
    else:
        name = name[:max_len].rstrip(' .')
    if name.split('.')[0].upper() in _RESERVED:
        name = '_' + name
    return name


def unique_path(folder: str, filename: str) -> str:
    """Return folder/filename, adding ' (2)', ' (3)'... if that name is taken."""
    path = os.path.join(folder, filename)
    if not os.path.exists(path) and not os.path.exists(path + '.aria2'):
        return path
    stem, ext = os.path.splitext(filename)
    for i in range(2, 10000):
        path = os.path.join(folder, f'{stem} ({i}){ext}')
        if not os.path.exists(path) and not os.path.exists(path + '.aria2'):
            return path
    return os.path.join(folder, f'{stem} ({int(time.time())}){ext}')


# --------------------------------------------------------------------------- Windows shell

# Zero everywhere else on purpose: subprocess rejects a non-zero creationflags
# outright off Windows, so the call sites can pass this without asking first.
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0


def hide_child_consoles():
    """Stop console windows flashing up for aria2c/ffmpeg/deno in the windowed exe."""
    if os.name != 'nt' or getattr(subprocess.Popen, '_grabbit_patched', False):
        return
    original_init = subprocess.Popen.__init__

    def patched_init(self, *args, **kwargs):
        if not kwargs.get('creationflags'):
            kwargs['creationflags'] = CREATE_NO_WINDOW
        return original_init(self, *args, **kwargs)

    subprocess.Popen.__init__ = patched_init
    subprocess.Popen._grabbit_patched = True


def ipv6_available() -> bool:
    """True when this machine can actually reach the IPv6 internet.

    Without this check aria2 tries a tracker's IPv6 address first, gets an
    instant 'network unreachable', and aborts the announce before its IPv4
    fallback can run - so torrents never find peers. The UDP connect below
    sends no packets; it only asks the routing table for a source address.
    """
    import socket
    for target in (('2001:4860:4860::8888', 53), ('2606:4700:4700::1111', 53)):
        try:
            with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock:
                sock.settimeout(0.4)
                sock.connect(target)
                local = sock.getsockname()[0]
            # Link-local or unique-local (e.g. a VPN's ULA) is not real connectivity.
            if local and not local.startswith(('fe80', 'fd', 'fc', '::')):
                return True
        except OSError:
            continue
    return False


def open_path(path: str) -> bool:
    if path and os.path.exists(path):
        if IS_WINDOWS:
            os.startfile(path)  # noqa: S606 - opening a user's own file
        else:
            subprocess.Popen(['xdg-open', path])
        return True
    return False


def reveal_in_explorer(path: str) -> bool:
    if not path or not IS_WINDOWS:
        return open_path(os.path.dirname(path) if path else '')
    if os.path.exists(path):
        subprocess.Popen(['explorer', '/select,', os.path.normpath(path)])
        return True
    folder = os.path.dirname(path)
    while folder and not os.path.isdir(folder):
        parent = os.path.dirname(folder)
        if parent == folder:
            return False
        folder = parent
    if folder:
        os.startfile(folder)  # noqa: S606
        return True
    return False


def send_to_recycle_bin(paths: list[str]) -> bool:
    """Move files/folders to the Recycle Bin (no confirmation UI). True on success."""
    existing = [os.path.abspath(p) for p in paths if p and os.path.exists(p)]
    if not existing:
        return True
    if not IS_WINDOWS:
        return False

    from ctypes import wintypes

    class _SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ('hwnd', wintypes.HWND),
            ('wFunc', wintypes.UINT),
            ('pFrom', wintypes.LPCWSTR),
            ('pTo', wintypes.LPCWSTR),
            ('fFlags', ctypes.c_uint16),
            ('fAnyOperationsAborted', wintypes.BOOL),
            ('hNameMappings', ctypes.c_void_p),
            ('lpszProgressTitle', wintypes.LPCWSTR),
        ]

    FO_DELETE, FOF_ALLOWUNDO, FOF_NOCONFIRMATION, FOF_SILENT, FOF_NOERRORUI = 3, 0x40, 0x10, 0x4, 0x400
    op = _SHFILEOPSTRUCTW()
    op.wFunc = FO_DELETE
    op.pFrom = '\0'.join(existing) + '\0\0'
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    return ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) == 0
