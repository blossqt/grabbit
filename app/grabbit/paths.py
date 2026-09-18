"""Where Grabbit keeps its data and finds its bundled tools."""

import os
import shutil
import sys
from pathlib import Path

from . import APP_NAME


def is_frozen() -> bool:
    return bool(getattr(sys, 'frozen', False))


def app_dir() -> Path:
    """Folder containing Grabbit.exe (frozen) or the project root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def data_dir() -> Path:
    """Per-user state: settings, task list, torrent metadata, logs.

    A ``portable.txt`` file next to Grabbit.exe keeps everything in ``data``
    beside the exe instead of %LOCALAPPDATA%.
    """
    if (app_dir() / 'portable.txt').exists():
        path = app_dir() / 'data'
    else:
        base = os.environ.get('LOCALAPPDATA') or str(Path.home() / 'AppData' / 'Local')
        path = Path(base) / APP_NAME
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


def engines_dir() -> Path:
    """Holds updated yt-dlp builds downloaded by the in-app updater."""
    path = data_dir() / 'engines'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tool_candidates(name: str):
    root = app_dir()
    yield root / 'tools' / f'{name}.exe'
    yield root / 'tools' / name / f'{name}.exe'
    if name in ('ffmpeg', 'ffprobe'):
        yield root / 'tools' / 'ffmpeg' / f'{name}.exe'
    if not is_frozen():
        # Development checkout: aria2c.exe from build_aria2.sh, the rest from bootstrap.ps1.
        yield root / 'vendor' / f'{name}.exe'
        components = Path(os.environ.get('LOCALAPPDATA', '')) / 'GrabbitBuild' / 'components'
        yield components / name / f'{name}.exe'
        if name in ('ffmpeg', 'ffprobe'):
            yield components / 'ffmpeg' / f'{name}.exe'


def find_tool(name: str) -> str | None:
    """Locate aria2c, ffmpeg, ffprobe or deno; bundled copies win over PATH."""
    for candidate in _tool_candidates(name):
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)
