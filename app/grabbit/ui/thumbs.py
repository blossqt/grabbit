"""Background loading of preview images, with a small in-memory cache."""

import logging
import urllib.request
from collections import OrderedDict

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Qt, Signal
from PySide6.QtGui import QImage, QPixmap

log = logging.getLogger(__name__)

_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
MAX_BYTES = 12 * 1024 * 1024


class _Signals(QObject):
    done = Signal(str, QImage)


class _Fetch(QRunnable):
    def __init__(self, url: str, headers: dict, signals: _Signals):
        super().__init__()
        self.url = url
        self.headers = headers or {}
        self.signals = signals

    def run(self):
        image = QImage()
        try:
            request = urllib.request.Request(self.url, headers={'User-Agent': _UA, **self.headers})
            # Kept short: Qt waits for pooled threads when the app exits, so a
            # thumbnail still fetching would hold the close-down open.
            with urllib.request.urlopen(request, timeout=8) as response:
                data = response.read(MAX_BYTES)
            image.loadFromData(data)
        except Exception as exc:
            log.debug('thumbnail failed %s: %s', self.url[:80], exc)
        self.signals.done.emit(self.url, image)


class ThumbnailLoader(QObject):
    """Fetches images off the GUI thread; `ready` fires with a scaled pixmap."""

    ready = Signal(str, QPixmap)

    def __init__(self, parent=None, max_cache: int = 400):
        super().__init__(parent)
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(6)
        self._cache: OrderedDict = OrderedDict()
        self._pending: set = set()
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._max_cache = max_cache

    def cached(self, url: str) -> QPixmap | None:
        pixmap = self._cache.get(url)
        if pixmap is not None:
            self._cache.move_to_end(url)
        return pixmap

    def request(self, url: str, headers: dict | None = None) -> QPixmap | None:
        """Returns the pixmap if cached, else starts a fetch and returns None."""
        if not url:
            return None
        pixmap = self.cached(url)
        if pixmap is not None:
            return pixmap
        if url not in self._pending:
            self._pending.add(url)
            self._pool.start(_Fetch(url, headers or {}, self._signals))
        return None

    def _on_done(self, url: str, image: QImage):
        self._pending.discard(url)
        if image.isNull():
            pixmap = QPixmap()
        else:
            if image.width() > 1400 or image.height() > 1400:
                image = image.scaled(1400, 1400, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pixmap = QPixmap.fromImage(image)
        self._cache[url] = pixmap
        while len(self._cache) > self._max_cache:
            self._cache.popitem(last=False)
        self.ready.emit(url, pixmap)


_loader: ThumbnailLoader | None = None


def loader() -> ThumbnailLoader:
    global _loader
    if _loader is None:
        _loader = ThumbnailLoader()
    return _loader
