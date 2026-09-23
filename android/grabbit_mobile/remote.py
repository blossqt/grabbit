"""The window's side of the downloader.

The downloads run in a process of their own (host.py), so that they outlive
the window. This offers the window everything MobileEngine would, done over
there: a copy of the download list is kept here to draw from - read from disk
at once, so the window opens with the list already in it, then brought up to
date from the downloader every second while the window is on screen - and
anything that changes a download is sent across and done there.

One thread does all the talking, in turn: whatever the window asked, and a
snapshot every second in between. If the downloader goes - it ends itself once
there is nothing to do and no window asking - it is started again.
"""

import logging
import queue
import socket
import threading
import time

from grabbit import analyze as analyze_mod
from grabbit.tasks import State, Task, TaskStore

from . import wire

log = logging.getLogger(__name__)

# How long to wait for the downloader to come up before asking Android to
# start it again.
PATIENCE = 12.0
# How often the window's copy is brought up to date while it is on screen.
EVERY = 1.0


class RemoteError(Exception):
    """The downloader was asked, and could not do it."""


class _ReadOnlyStore(TaskStore):
    """The window's copy: the downloader keeps the list on disk, not this."""

    def save(self, force: bool = False):
        pass


class RemoteEngine:
    """MobileEngine, as the window sees it, with the work done elsewhere.

    controls and context are DownloadService's static methods for the window
    and the activity they take (bootstrap.window_controls). schedule(fn) runs
    fn on the thread that draws; by default it runs at once.
    """

    def __init__(self, settings, on_change=None, on_message=None,
                 controls=None, context=None, schedule=None):
        self.settings = settings
        self.store = _ReadOnlyStore()
        self.store.load()
        self.on_change = on_change or (lambda: None)
        self.on_message = on_message or (lambda level, text: None)
        self.controls = controls
        self.context = context
        self._schedule = schedule or (lambda fn: fn())
        self.connected = False          # talking to a downloader
        self.running = False            # and its aria2 is up
        self.version = ''
        self.stats = {}
        self.held = False               # Wi-Fi only, waiting for Wi-Fi

        self._requests = queue.Queue()
        self._stop = threading.Event()
        self._active = True             # the window is on screen
        self._thread = None
        self._reader = None
        self._numbered = 0
        self._boot = None
        self._after = 0                 # the last message already shown
        self._latest = None             # a snapshot waiting to be drawn
        self._drawing = False           # and _draw is on its way for it
        self._handover = threading.Lock()
        self._launched = 0.0
        self._edited = 0.0              # when a tap last changed the copy here

    # ---------------------------------------------------------- starting
    def start(self) -> bool:
        """Start the downloader, if need be, and begin talking to it. From
        the main thread: Android starts a service only for an app on screen."""
        self.launch()
        self._thread = threading.Thread(target=self._talk, name='grabbit-remote', daemon=True)
        self._thread.start()
        return True

    def launch(self) -> bool:
        self._launched = time.monotonic()
        if self.controls is None:
            return False
        try:
            self.controls.begin(self.context)
            return True
        except Exception as exc:
            # Off screen, Android starts nothing; the next time on screen will.
            log.warning('could not start the downloader: %s', exc)
            return False

    def expect(self, title: str):
        """A download was just asked for: have the downloader in the
        foreground at once, while the app is certainly on screen."""
        if self.controls is None:
            return
        try:
            self.controls.expect(self.context, title or 'Download')
        except Exception as exc:
            log.warning('could not bring the downloader forward: %s', exc)

    def set_active(self, active: bool):
        """The window came on screen, or left it. Off screen it stops asking,
        which lets the downloader end itself once there is nothing to do; back
        on screen it asks at once, starting it again if it has gone."""
        self._active = active
        if active:
            self._requests.put(None)            # wake the talker for a snapshot

    def shutdown(self):
        """The window is closing. The downloads carry on regardless."""
        self._stop.set()
        self._requests.put(None)

    # --------------------------------------------------- what is asked for
    def analyze_link(self, url: str, on_done) -> None:
        """Reading a link happens here, in the window's process: nothing is
        fetched until a choice has been made, and there may be none."""
        def work():
            try:
                result = analyze_mod.analyze(url.strip(), self.settings)
            except Exception as exc:
                result = analyze_mod.Analysis(url=url, kind=analyze_mod.KIND_ERROR,
                                              error=f'Could not read that link: {exc}')
            on_done(result)

        threading.Thread(target=work, name='grabbit-analyze', daemon=True).start()

    def add_analysis(self, result, choice: dict | None = None) -> None:
        self._ask({'op': 'add', 'analysis': wire.encode(result), 'choice': dict(choice or {})})

    def pause(self, task_ids):
        self._mark(task_ids, State.PAUSED)
        self._ask({'op': 'pause', 'ids': list(task_ids)})

    def resume(self, task_ids):
        self._mark(task_ids, State.QUEUED)
        self._ask({'op': 'resume', 'ids': list(task_ids)})

    def remove(self, task_ids, delete_files: bool = False):
        self._edited = time.monotonic()
        for task_id in task_ids:
            self.store.remove(task_id)
        self._ask({'op': 'remove', 'ids': list(task_ids), 'delete_files': bool(delete_files)})
        self.on_change()

    def settings_changed(self):
        """The window saved new settings: the downloader reads them again."""
        self._ask({'op': 'settings'})

    def fetch_files(self, task, callback):
        self._query('aria2.getFiles', [task.gid], [], callback) if task.gid else callback([])

    def fetch_peers(self, task, callback):
        if task.gid and task.is_torrent:
            self._query('aria2.getPeers', [task.gid], [], callback)
        else:
            callback([])

    def fetch_status(self, task, keys, callback):
        self._query('aria2.tellStatus', [task.gid, keys], {}, callback) if task.gid else callback({})

    def fetch_log(self, task, callback):
        self._ask({'op': 'log', 'id': task.id}, lambda result: callback(result or []))

    def _query(self, method, args, default, callback):
        self._ask({'op': 'query', 'method': method, 'args': args},
                  lambda result: callback(result if result is not None else default))

    def _mark(self, task_ids, state):
        """Show a tap's effect at once; the next snapshot has the last word."""
        self._edited = time.monotonic()
        for task_id in task_ids:
            task = self.store.get(task_id)
            if task is not None:
                task.state = state
                if state == State.PAUSED:
                    task.down_speed = task.up_speed = 0
        self.on_change()

    def _ask(self, request: dict, callback=None):
        self._requests.put((request, callback))

    # ------------------------------------------------------------ talking
    def _talk(self):
        sock = None
        while not self._stop.is_set():
            if sock is None:
                sock = self._connect()
                if sock is None:
                    self._lost()
                    if self._active and time.monotonic() - self._launched > PATIENCE:
                        self.launch()
                    self._stop.wait(0.25 if self._active else 2.0)
                    continue
            try:
                self._exchange(sock)
            except (OSError, ValueError) as exc:
                log.info('lost the downloader: %s', exc)
                try:
                    sock.close()
                except OSError:
                    pass
                sock = None
                self._lost()
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _connect(self):
        found = wire.published()
        if not found:
            return None
        sock = None
        try:
            # Short waits until it has answered: the address may be left over
            # from a downloader that is gone, and its port taken by something
            # that will never say hello.
            sock = socket.create_connection(('127.0.0.1', int(found['port'])), timeout=3)
            wire.send(sock, {'secret': found['secret']})
            reader = wire.Reader(sock)
            hello = reader.read()
            if not hello or not hello.get('ok'):
                raise ValueError('not the downloader')
            sock.settimeout(60)
        except (OSError, ValueError):
            if sock is not None:
                sock.close()
            return None
        if hello.get('boot') != self._boot:
            self._boot, self._after = hello.get('boot'), 0      # a new downloader
        self._reader = reader
        self.connected = True
        return sock

    def _exchange(self, sock):
        """One turn: whatever was asked, then a snapshot while on screen."""
        try:
            item = self._requests.get(timeout=EVERY if self._active else 30.0)
        except queue.Empty:
            item = None
        while item is not None:
            request, callback = item
            try:
                result = self._call(sock, request)
            except RemoteError as exc:
                log.warning('the downloader could not %s: %s', request.get('op'), exc)
                self._schedule(lambda text=str(exc): self.on_message('error', text))
                result = None
            if callback is not None:
                callback(result)
            try:
                item = self._requests.get_nowait()
            except queue.Empty:
                item = None
        if self._active and not self._stop.is_set():
            asked = time.monotonic()
            self._take(self._call(sock, {'op': 'snapshot', 'after': self._after}), asked)

    def _call(self, sock, request: dict):
        self._numbered += 1
        request = dict(request, id=self._numbered)
        wire.send(sock, request)
        while True:
            reply = self._reader.read()
            if reply is None:
                raise OSError('the downloader closed the connection')
            if reply.get('id') == self._numbered:
                break
        if not reply.get('ok'):
            raise RemoteError(reply.get('error') or 'it failed')
        return reply.get('result')

    def _take(self, snapshot: dict, asked: float = 0.0):
        """Hand a snapshot to the thread that draws - only the newest, however
        many arrive while it is busy, with every message none of them showed."""
        snapshot['asked'] = asked
        messages = [entry for entry in snapshot.get('messages') or [] if entry[0] > self._after]
        if messages:
            self._after = max(entry[0] for entry in messages)
        with self._handover:
            pending, self._latest = self._latest, snapshot
            snapshot['messages'] = (pending['messages'] if pending else []) + messages
            schedule, self._drawing = not self._drawing, True
        if schedule:
            self._schedule(self._draw)

    def _draw(self):
        with self._handover:
            snapshot, self._latest = self._latest, None
            self._drawing = False
        if snapshot is None:
            return
        # One asked for before the last tap cannot know of it, and would undo
        # on screen - for a second - what the tap did.
        if snapshot.get('asked', 0.0) >= self._edited:
            seen = set()
            for state in snapshot.get('tasks') or []:
                task = self.store.get(state.get('id', ''))
                if task is None:
                    task = self.store.add(Task(id=state['id']))
                wire.apply_state(task, state)
                seen.add(task.id)
            for task in list(self.store):
                if task.id not in seen:
                    self.store.remove(task.id)
        self.stats = snapshot.get('stats') or {}
        self.version = snapshot.get('version') or ''
        self.running = bool(snapshot.get('running'))
        self.held = bool(snapshot.get('held'))
        for _, level, text in snapshot['messages']:
            self.on_message(level, text)
        self.on_change()

    def _lost(self):
        if self.connected or self.running:
            self.connected = self.running = False
            self.stats = {}
            self._schedule(self.on_change)
