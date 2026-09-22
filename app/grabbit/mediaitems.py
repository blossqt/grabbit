"""What a look at a link found, before anything is downloaded.

These live apart from media.py so that handling a result costs nothing to
import: media.py brings in yt-dlp, the largest thing Grabbit loads, and the
phone app opens without it (see its main.py).
"""

from dataclasses import dataclass, field


@dataclass
class MediaItem:
    key: str = ''
    kind: str = 'video'            # video | image | audio
    title: str = ''
    thumbnail: str = ''            # small, for the grid
    preview: str = ''              # large, for the preview window
    duration: float | None = None
    width: int = 0
    height: int = 0
    filesize: int | None = None
    ext: str = ''
    url: str = ''                  # page URL (playlist entries)
    direct_url: str = ''           # file URL (images/audio)
    filename: str = ''             # preferred name on disk, when the source knows one
    group: str = ''                # video | audio | image | file, for sorting/labels
    headers: dict = field(default_factory=dict)
    info: dict | None = None       # pre-extracted info, saves a second lookup
    index: int = 1
    optional: bool = False         # unticked by default (slideshow soundtrack)
    heights: list = field(default_factory=list)


@dataclass
class ProbeResult:
    url: str = ''
    kind: str = 'video'            # video | gallery | playlist
    site: str = ''
    title: str = ''
    uploader: str = ''
    thumbnail: str = ''
    description: str = ''
    items: list = field(default_factory=list)
    heights: list = field(default_factory=list)
    is_live: bool = False
    error: str = ''

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.items)
