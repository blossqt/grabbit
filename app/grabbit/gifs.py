"""A GIF's frames, saved as pictures: the one frame chosen, or every frame
into a folder of its own.

Qt reads the GIF. Its reader puts each frame together over the ones before
it, as a browser shows them, so a frame that only changes a corner still comes
out whole - and the frames chosen from in the add dialog are the very ones
saved. Desktop only: build\\release.py leaves this file out of the phone app's
inputs.
"""

from __future__ import annotations

import logging
import os
import threading
import time
import urllib.request

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, Qt
from PySide6.QtGui import QImage, QImageReader, QPainter

from .util import safe_filename, unique_path

log = logging.getLogger(__name__)

# More than any GIF a site would show; past it, something else is being sent.
MAX_BYTES = 200 * 1024 * 1024
CHUNK = 64 * 1024
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

ALL = 'all'           # which frames: every one, or a frame's index from 0


class GifError(RuntimeError):
    """The GIF could not be fetched or read."""


class Stopped(Exception):
    """Paused or removed while it was being worked on: args[0] says which."""


def is_gif(content_type: str, name: str = '') -> bool:
    return (content_type or '').lower() == 'image/gif' or (name or '').lower().endswith('.gif')


def fetch(url: str, headers: dict | None = None, progress=None, check=None,
          timeout: float = 30) -> bytes:
    """The GIF's bytes. progress(done, total) as they come; check() may raise
    Stopped between chunks."""
    request = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': 'image/*,*/*',
                                                   **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = int(response.headers.get('Content-Length') or 0)
            if total > MAX_BYTES:
                raise GifError('That GIF is too big to take apart')
            data = bytearray()
            while True:
                if check:
                    check()
                chunk = response.read(CHUNK)
                if not chunk:
                    break
                data += chunk
                if len(data) > MAX_BYTES:
                    raise GifError('That GIF is too big to take apart')
                if progress:
                    progress(len(data), total)
    except (GifError, Stopped):
        raise
    except Exception as exc:
        raise GifError(f'Could not fetch the GIF: {exc}') from None
    return bytes(data)


def frames(data: bytes):
    """Each frame in turn, whole, as a QImage."""
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.ReadOnly)
    reader = QImageReader(buffer, b'gif')
    reader.setDecideFormatFromContent(True)
    count = 0
    while True:
        image = reader.read()
        if image.isNull():
            break
        count += 1
        yield image
    if not count:
        raise GifError(f'That is not a GIF Qt can read: {reader.errorString()}')


def frame_name(title: str, index: int, count: int, kind: str) -> str:
    """'Title - frame 07.png': numbered from 1, padded so they sort in order."""
    width = len(str(max(1, count)))
    return f'{safe_filename(title, "gif", 150)} - frame {index + 1:0{width}d}.{kind}'


def folder_name(title: str) -> str:
    return f'{safe_filename(title, "gif", 150)} frames'


def save(image: QImage, path: str, kind: str) -> str:
    """Write one frame, PNG or JPG. A JPG has no see-through parts, so those turn white,
    as they would on a page."""
    if kind == 'jpg' and image.hasAlphaChannel():
        flat = QImage(image.size(), QImage.Format_RGB32)
        flat.fill(Qt.white)
        painter = QPainter(flat)
        painter.drawImage(0, 0, image)
        painter.end()
        image = flat
    # Written under another name and renamed, so a half-written picture never
    # sits where the finished one should be.
    partial = path + '.part'
    if not image.save(partial, 'JPG' if kind == 'jpg' else 'PNG', 95 if kind == 'jpg' else -1):
        try:
            os.remove(partial)
        except OSError:
            pass
        raise GifError(f'Could not write {os.path.basename(path)}')
    os.replace(partial, path)
    return path


def frame_count(data: bytes) -> int:
    return sum(1 for _ in frames(data))


def save_frames(data: bytes, save_dir: str, title: str, which, kind: str,
                progress=None, check=None, started=None) -> str:
    """Save frame `which` (an index), or ALL of them into a folder of their own.
    Returns what was made: the picture, or the folder. started(path) is told
    of it before anything is written there."""
    count = frame_count(data)
    if which == ALL:
        folder = unique_path(save_dir, folder_name(title))
        if started:
            started(folder)
        os.makedirs(folder, exist_ok=True)
        for index, image in enumerate(frames(data)):
            if check:
                check()
            save(image, os.path.join(folder, frame_name(title, index, count, kind)), kind)
            if progress:
                progress(index + 1, count)
        return folder
    index = min(max(0, int(which)), count - 1)
    target = unique_path(save_dir, frame_name(title, index, count, kind))
    if started:
        started(target)
    for at, image in enumerate(frames(data)):
        if at == index:
            return save(image, target, kind)
    raise GifError('That frame is not in the GIF')


class GifJob(threading.Thread):
    """Fetches a GIF and saves its frames, reporting as a MediaJob does, so
    the engine queues, pauses and removes it like any media download."""

    def __init__(self, task, settings, callbacks):
        super().__init__(daemon=True, name=f'gif-{task.id}')
        self.task_id = task.id
        self.url = task.source
        self.save_dir = task.save_dir or settings.download_dir
        self.media = dict(task.media or {})
        self._emit = callbacks['emit']
        self.cancelled = False
        self.paused = False
        self._last = 0.0

    def pause(self):
        self.paused = True

    def resume(self):
        self.paused = False

    def cancel(self):
        self.cancelled = True

    def _check(self):
        if self.cancelled:
            raise Stopped('cancelled')
        if self.paused:
            raise Stopped('paused')

    def _check_removed(self):
        # Saving takes moments, and a pause halfway would leave half a folder behind.
        if self.cancelled:
            raise Stopped('cancelled')

    def _fetched(self, done: int, total: int):
        now = time.monotonic()
        if now - self._last > 0.3 or done == total:
            self._last = now
            self._emit(self.task_id, 'progress', {'done': done, 'total': total,
                                                  'note': 'Fetching the GIF'})

    def _saved(self, done: int, count: int):
        now = time.monotonic()
        if now - self._last > 0.3 or done == count:
            self._last = now
            self._emit(self.task_id, 'state', {'state': 'processing',
                                               'note': f'Saving frame {done} of {count}'})

    def run(self):
        try:
            os.makedirs(self.save_dir, exist_ok=True)
            self._emit(self.task_id, 'state', {'state': 'downloading', 'note': 'Fetching the GIF'})
            data = fetch(self.url, self.media.get('headers'), self._fetched, self._check)
            self._check()
            self._emit(self.task_id, 'state', {'state': 'processing', 'note': 'Saving frames'})
            made = save_frames(data, self.save_dir, self.media.get('title') or 'gif',
                               self.media.get('gif', ALL), self.media.get('frame_format', 'png'),
                               progress=self._saved, check=self._check_removed,
                               started=lambda path: self._emit(self.task_id, 'file', {'path': path}))
            if os.path.isdir(made):
                size = sum(entry.stat().st_size for entry in os.scandir(made) if entry.is_file())
            else:
                size = os.path.getsize(made)
            self._emit(self.task_id, 'finished', {'filepath': made, 'size': size})
        except Stopped as stop:
            self._emit(self.task_id, 'cancelled' if stop.args[0] == 'cancelled' else 'paused', {})
        except GifError as exc:
            self._emit(self.task_id, 'error', {'message': str(exc)})
        except Exception as exc:                      # never silently
            log.exception('saving GIF frames failed')
            self._emit(self.task_id, 'error', {'message': f'Could not save the frames: {exc}'})
