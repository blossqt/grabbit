"""Keeping the downloads going while Grabbit is not on screen.

Android freezes an app soon after it leaves the screen unless it runs a
foreground service, and closing the window - swiping Grabbit away included -
ends the process the window runs in. So the downloads live in a process of
their own, in DownloadService (build/android/java), which sits in the
foreground with Android's own progress notification whenever anything is
downloading or seeding. This decides when that is, and what the notification
says. The engine calls follow() after every poll, from its own thread, in the
downloader's process (host.py).
"""

import logging
import threading
import time

from grabbit.tasks import State
from grabbit.util import human_speed

log = logging.getLogger(__name__)

# What keeps the downloader in the foreground: something to fetch, or on its
# way, or a torrent giving back what it took.
KEEP = (State.QUEUED, State.METADATA, State.EXTRACTING, State.DOWNLOADING,
        State.PROCESSING, State.CHECKING, State.SEEDING)

# Once Android has refused to bring the service into the foreground - it does,
# with the app off screen - how long before asking again.
RETRY = 30.0


def describe(busy, stats=None) -> tuple:
    """(title, text, percent, seeding) for the notification: the download
    itself when there is one, how many when there are several. percent is -1
    until a size is known, and -2 - no bar - when all that is left is
    seeding."""
    stats = stats or {}
    fetching = [t for t in busy if t.state != State.SEEDING]
    seeding = [t for t in busy if t.state == State.SEEDING]
    if not fetching:
        up = human_speed(stats.get('upload_speed'))
        title = seeding[0].name or 'Torrent' if len(seeding) == 1 else f'{len(seeding)} torrents'
        return title, 'Seeding' + (f' · ↑ {up}' if up else ''), -2, True

    sized = [t for t in fetching if t.total > 0]
    total = sum(t.total for t in sized)
    done = sum(min(t.done, t.total) for t in sized)
    percent = min(100, int(done * 100 / total)) if total else -1
    speed = human_speed(stats.get('download_speed'))
    if len(fetching) == 1:
        title = fetching[0].name or fetching[0].source or 'Download'
        waiting = fetching[0].status_text        # "Reading page", "Waiting for Wi-Fi"...
    else:
        title = f'{len(fetching)} downloads'
        waiting = 'Starting'
    moving = ([f'{percent}%'] if percent >= 0 else []) + ([speed] if speed else [])
    text = ' · '.join(moving) if moving and any(t.state == State.DOWNLOADING for t in fetching) else waiting
    if seeding:
        text += f' · {len(seeding)} seeding'
    return title, text, percent, False


class Background:
    """Brings DownloadService into the foreground and out again, and keeps its
    notification current. Does nothing off a phone.

    The service - DownloadService's static methods - and the clock can be
    handed in, so the rules can be checked without a phone
    (build/android/preview_ui.py).
    """

    def __init__(self, service=None, clock=time.monotonic):
        self._service = service
        self._clock = clock
        self._lock = threading.Lock()
        self._shown = None              # what the notification last said
        self._refused_at = None

    def follow(self, tasks, stats=None):
        """In the foreground while anything is moving, saying how it is going."""
        if self._service is None:
            return
        busy = [t for t in tasks if t.state in KEEP]
        with self._lock:
            foreground = self._service.isForeground()
            if not busy:
                # False while a download asked for a moment ago is still on
                # its way to the engine: the service holds on for it.
                if foreground and self._service.hide():
                    self._shown = None
                return
            content = describe(busy, stats)
            if foreground and content == self._shown:
                return
            now = self._clock()
            if not foreground and self._refused_at is not None and now - self._refused_at < RETRY:
                return
            if self._service.show(*content):
                self._shown = content
                self._refused_at = None
            elif not foreground:
                if self._refused_at is None:
                    log.warning('Android would not keep the downloads going in the background')
                self._refused_at = now

    def stop(self):
        """The downloader is ending: out of the foreground, notification and all."""
        if self._service is not None:
            with self._lock:
                try:
                    self._service.hide()
                except Exception:
                    log.exception('could not leave the foreground')
                self._shown = None
