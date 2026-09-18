"""Where things live on a phone.

Android gives an app three useful places, and they are not interchangeable:
  - a private folder nothing else can read, which is the only place an
    executable may be run from, so aria2 lives there;
  - an app-specific folder on shared storage that needs no permission;
  - the real Downloads folder, which does need one.

Off-device (running the engine under WSL for testing) these fall back to
ordinary directories so the same code can be exercised without a phone.
"""

import os
from pathlib import Path

ON_ANDROID = 'ANDROID_ARGUMENT' in os.environ or os.path.isdir('/system/bin')


def _android_private() -> Path:
    # p4a exports this; it is /data/data/<package>/files
    private = os.environ.get('ANDROID_PRIVATE')
    if private:
        return Path(private)
    return Path.home() / '.grabbit'


def data_dir() -> Path:
    """Settings, the task list and torrent metadata."""
    path = _android_private() / 'grabbit'
    path.mkdir(parents=True, exist_ok=True)
    return path


def tools_dir() -> Path:
    """Executables unpacked from the APK. Must be app-private to be runnable."""
    path = _android_private() / 'tools'
    path.mkdir(parents=True, exist_ok=True)
    return path


def torrents_dir() -> Path:
    path = data_dir() / 'torrents'
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    path = data_dir() / 'logs'
    path.mkdir(parents=True, exist_ok=True)
    return path


def downloads_dir() -> Path:
    """Where finished files go.

    Prefers the real Downloads folder when the app has permission, and falls
    back to the app-specific folder on shared storage, which always works and
    is still visible over USB.
    """
    if ON_ANDROID:
        shared = Path('/storage/emulated/0/Download/Grabbit')
        try:
            shared.mkdir(parents=True, exist_ok=True)
            probe = shared / '.writable'
            probe.touch()
            probe.unlink()
            return shared
        except OSError:
            pass
        external = os.environ.get('ANDROID_APP_PATH') or str(_android_private())
        path = Path(external) / 'Download'
    else:
        path = Path.home() / 'Downloads' / 'Grabbit'
    path.mkdir(parents=True, exist_ok=True)
    return path


# Filled in by bootstrap.unpack_tools() once the real locations are known.
TOOL_PATHS: dict = {}


def find_tool(name: str) -> str | None:
    """Locate a bundled executable (aria2c, ffmpeg, quickjs)."""
    known = TOOL_PATHS.get(name)
    if known and os.path.isfile(known):
        return known
    candidate = tools_dir() / name
    if candidate.is_file():
        return str(candidate)
    import shutil
    return shutil.which(name)


def adopt_shared_paths() -> None:
    """Make the shared modules keep their state in the phone's folders.

    The task list, settings and cached torrents are handled by code that was
    written for a desktop and would otherwise put them somewhere Windows-shaped
    that means nothing here.
    """
    from grabbit import paths as shared
    shared.set_data_dir(data_dir())


try:
    adopt_shared_paths()
except ImportError:      # the shared package is not on the path yet
    pass
