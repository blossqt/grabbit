"""Re-serves a remote stream on localhost so any video player can open it.

Media URLs often only work with the right Referer, User-Agent and session
cookies, and most players offer no way to set those. Grabbit publishes the
stream at http://127.0.0.1:<port>/<token>/<name> and adds the headers itself
while passing Range requests straight through, so seeking still works.

Only loopback is bound, and a URL is reachable only via its random token.
"""

import logging
import secrets
import shutil
import subprocess
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .paths import find_tool
from .util import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

PASS_THROUGH = ('Content-Type', 'Content-Length', 'Content-Range', 'Accept-Ranges',
                'Last-Modified', 'ETag')


class _Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'Grabbit'

    def do_GET(self):
        self._serve(with_body=True)

    def do_HEAD(self):
        self._serve(with_body=False)

    def _serve(self, with_body: bool):
        token = urllib.parse.urlparse(self.path).path.strip('/').split('/')[0]
        entry = self.server.streams.get(token)
        if not entry:
            self.send_error(404)
            return
        if entry.get('mux'):
            self._serve_muxed(entry, with_body)
            return

        headers = dict(entry['headers'])
        for name in ('Range', 'If-Range'):
            value = self.headers.get(name)
            if value:
                headers[name] = value

        request = urllib.request.Request(entry['url'], headers=headers,
                                         method='GET' if with_body else 'HEAD')
        try:
            upstream = urllib.request.urlopen(request, timeout=30)
        except urllib.error.HTTPError as exc:
            log.warning('preview upstream said %s', exc.code)
            self.send_error(exc.code)
            return
        except Exception as exc:
            log.warning('preview upstream failed: %s', exc)
            self.send_error(502)
            return

        with upstream:
            self.send_response(upstream.status)
            for name in PASS_THROUGH:
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            if not upstream.headers.get('Accept-Ranges'):
                self.send_header('Accept-Ranges', 'bytes')
            self.end_headers()
            if not with_body:
                return
            try:
                shutil.copyfileobj(upstream, self.wfile, 256 * 1024)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass          # the player seeked away or was closed

    def _serve_muxed(self, entry: dict, with_body: bool):
        """Join separate video and audio tracks with ffmpeg as they play.

        Sites that only offer split tracks (most of YouTube) have nothing a
        player can open on its own. Copying both into a Matroska stream keeps
        it instant - nothing is re-encoded - at the cost of seeking.
        """
        ffmpeg = find_tool('ffmpeg')
        if not ffmpeg:
            self.send_error(501, 'ffmpeg not available')
            return
        self.send_response(200)
        self.send_header('Content-Type', 'video/x-matroska')
        self.end_headers()
        if not with_body:
            return

        header_block = ''.join(f'{k}: {v}\r\n' for k, v in entry['headers'].items() if v)
        args = [ffmpeg, '-loglevel', 'error', '-nostdin']
        for source in entry['mux']:
            if header_block:
                args += ['-headers', header_block]
            args += ['-i', source]
        args += ['-map', '0:v:0', '-map', '1:a:0', '-c', 'copy', '-f', 'matroska', 'pipe:1']

        process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                   creationflags=CREATE_NO_WINDOW)
        try:
            shutil.copyfileobj(process.stdout, self.wfile, 256 * 1024)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass              # player closed or seeked
        finally:
            try:
                process.kill()
            except Exception:
                pass

    def log_message(self, *args):
        pass                  # keep the console quiet


class StreamServer:
    """Publishes streams for local players. Start once, reuse."""

    def __init__(self):
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.port = 0
        self.streams: dict = {}

    def start(self) -> bool:
        if self._server is not None:
            return True
        try:
            self._server = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        except OSError as exc:
            log.error('could not start the preview server: %s', exc)
            return False
        self._server.daemon_threads = True
        self._server.streams = self.streams
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever,
                                        name='grabbit-stream', daemon=True)
        self._thread.start()
        log.info('preview server on port %s', self.port)
        return True

    def publish(self, url: str, headers: dict | None = None, filename: str = 'video.mp4') -> str:
        """Register a remote stream and return its local URL."""
        if not self.start():
            return url
        token = secrets.token_urlsafe(12)
        self.streams[token] = {'url': url, 'headers': dict(headers or {})}
        # The name is only a hint so players can guess the container.
        return f'http://127.0.0.1:{self.port}/{token}/{urllib.parse.quote(filename)}'

    def publish_muxed(self, video_url: str, audio_url: str, headers: dict | None = None,
                      filename: str = 'video.mkv') -> str:
        """Publish a video and an audio track as one joined stream."""
        if not self.start():
            return video_url
        token = secrets.token_urlsafe(12)
        self.streams[token] = {'mux': [video_url, audio_url], 'headers': dict(headers or {})}
        return f'http://127.0.0.1:{self.port}/{token}/{urllib.parse.quote(filename)}'

    def forget(self, local_url: str):
        token = urllib.parse.urlparse(local_url).path.strip('/').split('/')[0]
        self.streams.pop(token, None)

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        self.streams.clear()


_server: StreamServer | None = None


def server() -> StreamServer:
    global _server
    if _server is None:
        _server = StreamServer()
    return _server
