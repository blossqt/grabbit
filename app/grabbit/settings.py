"""User settings, stored as JSON in the data folder."""

import json
import os
import random
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from .paths import data_dir


def _default_download_dir() -> str:
    try:
        import ctypes
        # FOLDERID_Downloads, so a moved Downloads folder is respected.
        guid = (ctypes.c_byte * 16).from_buffer_copy(
            bytes.fromhex('90e24d373f126545916439c4925e467b'))
        path = ctypes.c_wchar_p()
        if ctypes.windll.shell32.SHGetKnownFolderPath(guid, 0, None, ctypes.byref(path)) == 0:
            result = path.value
            ctypes.windll.ole32.CoTaskMemFree(path)
            if result:
                return result
    except Exception:
        pass
    return str(Path.home() / 'Downloads')


VIDEO_QUALITIES = [
    ('best', 'Best available'),
    ('2160', '2160p (4K)'),
    ('1440', '1440p'),
    ('1080', '1080p'),
    ('720', '720p'),
    ('480', '480p'),
    ('360', '360p'),
    ('audio_m4a', 'Audio only (M4A)'),
    ('audio_mp3', 'Audio only (MP3)'),
]

COOKIE_BROWSERS = [
    ('', 'Don’t use browser cookies'),
    ('firefox', 'Firefox'),
    ('chrome', 'Chrome'),
    ('edge', 'Microsoft Edge'),
    ('brave', 'Brave'),
    ('opera', 'Opera'),
    ('vivaldi', 'Vivaldi'),
    ('chromium', 'Chromium'),
]


@dataclass
class Settings:
    # Downloads
    download_dir: str = field(default_factory=_default_download_dir)
    max_active_downloads: int = 5
    max_media_jobs: int = 3
    connections_per_server: int = 16
    min_split_size_mb: int = 1
    download_limit_kib: int = 0
    upload_limit_kib: int = 0
    user_agent: str = ''
    proxy: str = ''
    ipv6_mode: str = 'auto'   # auto | on | off

    # BitTorrent
    bt_port: int = field(default_factory=lambda: random.randint(20000, 60000))
    enable_dht: bool = True
    enable_pex: bool = True
    enable_lpd: bool = True
    require_encryption: bool = False
    seed_after_download: bool = True
    seed_ratio: float = 1.0
    seed_time_minutes: int = 0
    max_peers: int = 55
    extra_trackers: str = ''
    show_torrent_dialog: bool = True

    # Videos & images
    video_quality: str = 'best'
    video_container: str = 'mp4'
    embed_thumbnail: bool = True
    embed_metadata: bool = True
    download_subtitles: bool = False
    subtitle_langs: str = 'en.*'
    filename_template: str = '%(title).150B [%(id)s].%(ext)s'
    site_subfolders: bool = False
    cookies_browser: str = ''
    cookies_file: str = ''
    media_via_aria2: bool = True

    # Interface
    watch_clipboard: bool = True
    magnet_handler: bool = False
    close_to_tray: bool = False
    notify_on_complete: bool = True
    confirm_remove: bool = True
    theme: str = 'system'
    recent_dirs: list = field(default_factory=list)
    window_geometry: str = ''
    window_state: str = ''
    header_state: str = ''
    splitter_state: str = ''
    details_splitter_state: str = ''

    # Advanced
    aria2_extra_options: str = ''

    @property
    def path(self) -> Path:
        return data_dir() / 'settings.json'

    @classmethod
    def load(cls) -> 'Settings':
        settings = cls()
        try:
            raw = json.loads((data_dir() / 'settings.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            settings.save()  # persist generated defaults such as the BT port
            return settings
        known = {f.name: f for f in fields(cls)}
        for key, value in raw.items():
            if key in known:
                default = getattr(settings, key)
                if isinstance(default, bool) and not isinstance(value, bool):
                    continue
                if isinstance(default, (int, float)) and not isinstance(default, bool):
                    try:
                        value = type(default)(value)
                    except (TypeError, ValueError):
                        continue
                setattr(settings, key, value)
        return settings

    def save(self):
        path = data_dir() / 'settings.json'
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(asdict(self), indent=2), encoding='utf-8')
        os.replace(tmp, path)

    def remember_dir(self, folder: str):
        folder = os.path.normpath(folder)
        dirs = [d for d in self.recent_dirs if os.path.normcase(d) != os.path.normcase(folder)]
        self.recent_dirs = [folder, *dirs][:8]
