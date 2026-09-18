"""Windows file/protocol associations (per-user, no admin rights needed).

Only HKEY_CURRENT_USER\\Software\\Classes is touched, which is where an app is
supposed to register itself. Nothing here writes to HKEY_LOCAL_MACHINE or
changes another program's association: .torrent is added as an extra choice in
"Open with", never taken over.
"""

import logging
import sys
import winreg

from .paths import app_dir, is_frozen

log = logging.getLogger(__name__)

PROGID = 'Grabbit.torrent'
CLASSES = r'Software\Classes'


def launcher_command() -> str:
    """Command line Windows should run for a magnet link or torrent file."""
    if is_frozen():
        return f'"{sys.executable}" "%1"'
    exe = app_dir() / 'dist' / 'Grabbit' / 'Grabbit.exe'
    if exe.exists():
        return f'"{exe}" "%1"'
    # Development fallback: run the module with the current interpreter.
    return f'"{sys.executable}" "{app_dir() / "app" / "main.py"}" "%1"'


def _icon() -> str:
    if is_frozen():
        return f'{sys.executable},0'
    exe = app_dir() / 'dist' / 'Grabbit' / 'Grabbit.exe'
    return f'{exe},0' if exe.exists() else ''


def _write(path: str, value: str, name: str = ''):
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)


def _read(path: str, name: str = '') -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            return winreg.QueryValueEx(key, name)[0]
    except OSError:
        return None


def _delete_tree(path: str):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path, 0, winreg.KEY_READ) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                _delete_tree(f'{path}\\{child}')
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
    except OSError:
        pass


def is_magnet_handler() -> bool:
    command = _read(rf'{CLASSES}\magnet\shell\open\command')
    return bool(command) and 'grabbit' in command.lower()


def set_magnet_handler(enabled: bool) -> bool:
    """Register (or drop) Grabbit as the magnet: handler. True if applied."""
    try:
        if not enabled:
            if is_magnet_handler():
                _delete_tree(rf'{CLASSES}\magnet')
            return True
        _write(rf'{CLASSES}\magnet', 'URL:BitTorrent Magnet Protocol')
        _write(rf'{CLASSES}\magnet', '', 'URL Protocol')
        icon = _icon()
        if icon:
            _write(rf'{CLASSES}\magnet\DefaultIcon', icon)
        _write(rf'{CLASSES}\magnet\shell\open\command', launcher_command())
        return True
    except OSError as exc:
        log.warning('could not change the magnet association: %s', exc)
        return False


def register_torrent_type() -> bool:
    """Offer Grabbit in the 'Open with' list for .torrent files."""
    try:
        _write(rf'{CLASSES}\{PROGID}', 'BitTorrent file')
        icon = _icon()
        if icon:
            _write(rf'{CLASSES}\{PROGID}\DefaultIcon', icon)
        _write(rf'{CLASSES}\{PROGID}\shell\open\command', launcher_command())
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER,
                                rf'{CLASSES}\.torrent\OpenWithProgids', 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, PROGID, 0, winreg.REG_NONE, b'')
        return True
    except OSError as exc:
        log.warning('could not register the torrent file type: %s', exc)
        return False


def windows_picked_another_app() -> bool:
    """True when Windows has a user choice for magnet links that isn't ours.

    Windows protects that choice with a signature, so an app cannot overwrite
    it; the user has to switch handlers in Settings > Default apps.
    """
    choice = _read(r'Software\Microsoft\Windows\Shell\Associations\UrlAssociations'
                   r'\magnet\UserChoice', 'ProgId')
    return bool(choice) and 'grabbit' not in choice.lower()
