"""Downloads that carry on while Grabbit is not on screen.

Android freezes an app soon after it leaves the screen, and aria2 with it,
unless the app runs a foreground service - which also puts Android's own
progress notification in the shade. The service is DownloadService
(build/android/java), in the app's own process; this decides when it runs and
what its notification says. The engine calls follow() after every poll, from
its own thread, so the notification keeps moving while the interface - paused
along with the app - does not.
"""

import logging
import threading
import time

from grabbit.tasks import State
from grabbit.util import human_speed

log = logging.getLogger(__name__)

# What keeps the phone at it: something to fetch, or about to be. Seeding does
# not - it waits until Grabbit is open again.
BUSY = (State.QUEUED, State.METADATA, State.EXTRACTING, State.DOWNLOADING,
        State.PROCESSING, State.CHECKING)

# Started from a tap, it stays this long even before the download it is for
# has reached the engine: Android starts it only while the app is on screen,
# and a person can leave the app straight after tapping Download.
HOLD = 15.0
# How long a start may take before it counts as refused, and how long after a
# refusal before trying again.
SETTLE = 5.0
RETRY = 30.0


def describe(busy, stats=None) -> tuple:
    """(title, text, percent) for the notification: the download itself when
    there is one, how many when there are several. percent is -1 until a
    size is known."""
    sized = [t for t in busy if t.total > 0]
    total = sum(t.total for t in sized)
    done = sum(min(t.done, t.total) for t in sized)
    percent = min(100, int(done * 100 / total)) if total else -1
    speed = human_speed((stats or {}).get('download_speed'))
    if len(busy) == 1:
        title = busy[0].name or busy[0].source or 'Download'
        waiting = busy[0].status_text        # "Reading page", "Merging video and audio"...
    else:
        title = f'{len(busy)} downloads'
        waiting = 'Starting'
    moving = ([f'{percent}%'] if percent >= 0 else []) + ([speed] if speed else [])
    if moving and any(t.state == State.DOWNLOADING for t in busy):
        return title, ' · '.join(moving), percent
    return title, waiting, percent


def _android_service():
    """DownloadService's three static methods, and the context they take.

    Declared rather than looked up, because pyjnius reads every method of a
    class it looks up - hundreds, for a Service - and found here, on the main
    thread, because a thread Python starts can find Android's own classes but
    not the app's.
    """
    try:
        from jnius import JavaClass, JavaStaticMethod, MetaJavaClass, autoclass
    except ImportError:
        return None, None                   # not a phone
    try:
        class DownloadService(JavaClass, metaclass=MetaJavaClass):
            __javaclass__ = 'com/grabbit/downloader/DownloadService'
            show = JavaStaticMethod('(Landroid/content/Context;Ljava/lang/String;'
                                    'Ljava/lang/String;I)V')
            hide = JavaStaticMethod('(Landroid/content/Context;)V')
            isRunning = JavaStaticMethod('()Z')
        return DownloadService, autoclass('org.kivy.android.PythonActivity').mActivity
    except Exception:
        log.exception('this build has no background service; downloads stop when Grabbit is left')
        return None, None


class Background:
    """Starts, updates and stops DownloadService. Does nothing off a phone.

    The service and the clock can be handed in, so the rules can be checked
    without a phone (build/android/preview_ui.py).
    """

    def __init__(self, service=None, context=None, clock=time.monotonic):
        if service is None:
            service, context = _android_service()
        self._service = service
        self._context = context
        self._clock = clock
        self._lock = threading.Lock()
        self._started_at = None         # asked to start, and not stopped since
        self._shown = None              # what the notification last said
        self._hold_until = 0.0
        self._refused_at = None

    def expect(self, title: str = ''):
        """A download was just asked for, from a tap: start now, while the app
        is certainly on screen, rather than when the engine gets to it."""
        if self._service is None:
            return
        with self._lock:
            self._hold_until = self._clock() + HOLD
            if self._started_at is None:
                self._start((title or 'Download', 'Starting', -1))

    def follow(self, tasks, stats=None):
        """Run while anything is downloading, saying how it is going."""
        if self._service is None:
            return
        busy = [t for t in tasks if t.state in BUSY]
        with self._lock:
            now = self._clock()
            if not busy:
                if self._started_at is not None and now >= self._hold_until:
                    self._hide()
                return
            content = describe(busy, stats)
            if self._started_at is None:
                self._start(content)
            elif self._service.isRunning():
                if content != self._shown:
                    self._show(content)
            elif now - self._started_at > SETTLE:
                # Asked to start and never did, or stopped since: Android
                # refused, or its six hours a day ran out. Try again later.
                self._started_at = None
                self._refused_at = now

    def stop(self):
        """Grabbit is closing: nothing is left to keep going."""
        if self._service is not None:
            with self._lock:
                self._hide()

    def _start(self, content):
        now = self._clock()
        if self._refused_at is not None and now - self._refused_at < RETRY:
            return
        try:
            self._service.show(self._context, *content)
        except Exception as exc:
            # Off screen, Android starts no foreground service.
            if self._refused_at is None:
                log.warning('could not keep downloading in the background: %s', exc)
            self._refused_at = now
            return
        self._started_at = now
        self._shown = content
        self._refused_at = None

    def _show(self, content):
        try:
            self._service.show(self._context, *content)
            self._shown = content
        except Exception:
            log.exception('could not update the download notification')

    def _hide(self):
        try:
            self._service.hide(self._context)
        except Exception:
            log.exception('could not stop the background service')
        self._started_at = None
        self._shown = None
