"""The downloader's side of Grabbit on a phone: aria2, every download, and the
socket the window asks through.

It runs in a process of its own - service.py, which python-for-android starts
as ServiceEngine (DownloadService.java; build_apk.sh) - so that closing the
window, swiping Grabbit out of the recent apps included, leaves the downloads
running. The window (remote.py) starts it and asks it for everything. It keeps
going for as long as there is something to fetch or seed, or the window is
asking, and ends itself once neither is true.
"""

import logging
import secrets
import socket
import socketserver
import threading
import time
from collections import deque

from grabbit.aria2rpc import Aria2Error
from grabbit.settings import Settings

from . import wire
from .background import KEEP, Background
from .engine import MobileEngine

log = logging.getLogger(__name__)

# With nothing to fetch or seed, how long the window may go without asking
# before the downloader ends itself. The window asks every second while it is
# on screen, so this is a minute after it was left.
IDLE = 60.0
# How often the main loop looks round: aria2 still running, Wi-Fi only, idle.
TICK = 1.0
# The details sheet's questions, which change nothing - the only aria2 calls
# the window may make directly.
QUERIES = {'aria2.getFiles', 'aria2.getPeers', 'aria2.tellStatus', 'aria2.getServers'}


class Host:
    """The engine, a socket for the window, and the rules for ending.

    service is DownloadService's static methods (bootstrap.service_controls);
    None, off a phone, leaves out the notification and the network questions,
    which is how build/android/engine_check.py runs it on a desktop.
    """

    def __init__(self, settings, service=None, clock=time.monotonic):
        self.settings = settings
        self.service = service
        self.clock = clock
        self.boot = secrets.token_hex(4)          # tells one run of it from the next
        self.secret = wire.new_secret()
        self.background = Background(service, clock=clock)
        self.engine = MobileEngine(settings, on_message=self._message,
                                   on_poll=self.background.follow)
        if service is not None:
            self.engine.online = service.online
        self.messages = deque(maxlen=40)          # (number, level, text)
        self._numbered = 0
        self._lock = threading.Lock()
        self.asked = clock()                      # when the window last asked anything
        self.moving = clock()                     # when there was last anything to do
        self.server = None
        self.stopping = False
        self.connections = set()                  # the windows talking to it

    # ------------------------------------------------------------ running
    def start(self) -> bool:
        """Start aria2 and open the socket; False when aria2 would not start -
        the window is still told why, over the socket."""
        started = self.engine.start()
        self._follow_network()
        self.server = _Server(('127.0.0.1', 0), self)
        threading.Thread(target=self.server.serve_forever, name='grabbit-host',
                         daemon=True).start()
        wire.publish(self.server.server_address[1], self.secret, self.boot)
        log.info('the downloader is up (port %s)', self.server.server_address[1])
        return started

    def run(self):
        """Look round every second, until there is no more reason to run."""
        while not self.stopping:
            time.sleep(TICK)
            try:
                self.tick()
            except Exception:
                log.exception('the downloader could not look round')

    def tick(self):
        now = self.clock()
        self.engine.ensure_running()
        self._follow_network()
        if any(task.state in KEEP for task in self.engine.store):
            self.moving = now
        if now - self.asked > IDLE and now - self.moving > IDLE:
            log.info('nothing to fetch or seed, and no window asking: stopping')
            self.stop()

    def stop(self):
        """Save everything, stop aria2, and end the service with its process."""
        if self.stopping and self.server is None:
            return
        self.stopping = True
        wire.withdraw(self.boot)
        try:
            self.engine.shutdown()          # the list saved, aria2 told to stop
        except Exception:
            log.exception('could not shut the engine down cleanly')
        self.background.stop()
        server, self.server = self.server, None
        if server is not None:
            server.shutdown()
            server.server_close()
        # As the process ending would: every window finds the line dead, and
        # starts a new downloader when it next wants one.
        for connection in list(self.connections):
            try:
                connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        if self.service is not None:
            self.service.finish()           # Android ends the process from here

    def _follow_network(self):
        """Wi-Fi only: hold everything back while the network costs by the byte."""
        held = (bool(getattr(self.settings, 'wifi_only', False))
                and self.service is not None and not self.service.unmetered())
        self.engine.hold(held)

    def _message(self, level: str, text: str):
        with self._lock:
            self._numbered += 1
            self.messages.append((self._numbered, level, text))

    # ------------------------------------------------------ what is asked
    def answer(self, request: dict):
        """One request from the window; what comes back goes in its reply."""
        self.asked = self.clock()
        op = request.get('op')
        engine = self.engine
        if op == 'snapshot':
            return self.snapshot(int(request.get('after') or 0))
        if op == 'add':
            engine.add_analysis(wire.decode(request['analysis']), request.get('choice') or {})
            return True
        if op == 'pause':
            engine.pause(list(request.get('ids') or []))
            return True
        if op == 'resume':
            engine.resume(list(request.get('ids') or []))
            return True
        if op == 'remove':
            engine.remove(list(request.get('ids') or []),
                          delete_files=bool(request.get('delete_files')))
            return True
        if op == 'query':
            if request.get('method') not in QUERIES or engine.client is None:
                return None
            try:
                return engine.client.call(request['method'], *(request.get('args') or []))
            except Aria2Error:
                return None             # a finished download aria2 has forgotten
        if op == 'log':
            task = engine.store.get(request.get('id', ''))
            return list(task.log[-200:]) if task else []
        if op == 'settings':
            self.settings = Settings.load()
            engine.apply_settings(self.settings)
            self._follow_network()
            return True
        if op == 'ping':
            return self.boot
        raise ValueError(f'nothing is done for {op!r}')

    def snapshot(self, after: int = 0) -> dict:
        """Everything the window draws, and the messages it has not had yet."""
        with self._lock:
            messages = [list(entry) for entry in self.messages if entry[0] > after]
        process = self.engine.process
        return {
            'boot': self.boot,
            'running': bool(self.engine.running and process and process.is_running()),
            'version': process.version if process else '',
            'stats': dict(self.engine.stats),
            'held': self.engine.held,
            'tasks': [wire.task_state(task) for task in self.engine.store],
            'messages': messages,
        }


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, host: Host):
        self.host = host
        super().__init__(address, _Conversation)


class _Conversation(socketserver.BaseRequestHandler):
    """One window's connection: the secret first, then request after request."""

    def handle(self):
        host = self.server.host
        host.connections.add(self.request)
        reader = wire.Reader(self.request)
        try:
            hello = reader.read()
            if not hello or not secrets.compare_digest(str(hello.get('secret', '')), host.secret):
                return
            wire.send(self.request, {'ok': True, 'boot': host.boot})
            while True:
                request = reader.read()
                if request is None:
                    return
                reply = {'id': request.get('id')}
                try:
                    reply['result'] = host.answer(request)
                    reply['ok'] = True
                except Exception as exc:
                    log.exception('could not do %r for the window', request.get('op'))
                    reply['ok'] = False
                    reply['error'] = str(exc) or exc.__class__.__name__
                wire.send(self.request, reply)
        except (OSError, ValueError) as exc:
            log.info('a window went away: %s', exc)
        finally:
            host.connections.discard(self.request)


def main():
    """service.py: the downloader's process, from start to finish."""
    logging.basicConfig(level=logging.INFO, format='%(name)s: %(message)s')
    from . import bootstrap
    # Declared first, on this thread: it is the only one that can find the
    # app's own Java classes.
    service = bootstrap.service_controls()
    try:
        bootstrap.unpack_tools()
    except Exception:
        log.exception('could not unpack the tools')
    host = Host(Settings.load(), service)
    try:
        host.start()
        host.run()
    finally:
        host.stop()
    # Android ends the process as the service stops. Were Python to finish
    # first, python-for-android would end it itself, and Android - finding a
    # sticky service gone without being stopped - would start it all again.
    time.sleep(10)
