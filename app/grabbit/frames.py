"""One frame of a video, saved as a picture - and the frames shown while
choosing it.

Choosing means looking at a frame each time the slider stops, so the stream is
found once - yt-dlp, a few seconds - and every frame after that is one short
FFmpeg run against it. FFmpeg seeks with range requests, so a frame from the
middle of an hour-long video costs a few hundred kilobytes, not the video. The
frame that is kept is read the same way from the sharpest stream on offer
(media.MediaJob), and downloading the whole video is only the fallback for a
stream FFmpeg cannot seek in.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field

from .paths import find_tool
from .util import CREATE_NO_WINDOW, safe_filename

log = logging.getLogger(__name__)

# What a frame can be saved as.
IMAGE_TYPES = [('png', 'PNG'), ('jpg', 'JPG')]

# Streams to look through while choosing: small, and one file over plain HTTP
# where there is one, since that is what FFmpeg seeks in quickly.
PREVIEW_FORMAT = ('bv*[height<=480][vcodec!^=av01][protocol^=http]'
                  '/b[height<=480][vcodec!^=av01][protocol^=http]'
                  '/bv*[height<=720][protocol^=http]/b[height<=720][protocol^=http]'
                  '/bv*[protocol^=http]/b[protocol^=http]/bv*/b')
# And the stream the kept frame comes from: the most pixels on offer.
FULL_FORMAT = 'bv*[protocol^=http]/b[protocol^=http]/bv*/b'

GRAB_TIMEOUT = 60


class FrameError(RuntimeError):
    """A frame could not be read."""


@dataclass
class Stream:
    """A video stream frames can be read from, with what it takes to fetch it."""
    url: str
    headers: dict = field(default_factory=dict)
    duration: float = 0.0
    fps: float = 0.0
    width: int = 0
    height: int = 0
    title: str = ''


def stream_from_info(info: dict, cookies=None) -> Stream:
    """The video stream a processed info dict settled on."""
    requested = info.get('requested_formats') or [info]
    video = next((f for f in requested if f.get('vcodec') not in (None, 'none')), requested[0])
    url = video.get('url') or info.get('url')
    if not url:
        raise FrameError('The site offered no stream to read a frame from')
    headers = dict(info.get('http_headers') or {})
    headers.update(video.get('http_headers') or {})
    if cookies is not None:
        # Some sites only serve a stream to the session that was handed it.
        try:
            cookie = cookies.get_cookie_header(url)
            if cookie:
                headers['Cookie'] = cookie
        except Exception:
            pass
    return Stream(url=url, headers=headers, duration=float(info.get('duration') or 0),
                  fps=float(video.get('fps') or info.get('fps') or 0),
                  width=int(video.get('width') or 0), height=int(video.get('height') or 0),
                  title=info.get('title') or '')


def find_stream(url: str, settings, fmt: str = PREVIEW_FORMAT, log_sink=None) -> Stream:
    """Ask the site for a stream to read frames from."""
    from . import media           # yt-dlp is slow to import; only when needed
    ydl = media.make_ydl(settings, {'format': fmt, 'skip_download': True,
                                    'noplaylist': True}, log_sink)
    try:
        with ydl:
            info = ydl.extract_info(url, download=False)
            if info.get('_type') in ('playlist', 'multi_video'):
                entries = [e for e in (info.get('entries') or []) if isinstance(e, dict)]
                if not entries:
                    raise FrameError('There is no video on that page')
                info = entries[0]
            stream = stream_from_info(info, ydl.cookiejar)
    except FrameError:
        raise
    except Exception as exc:
        raise FrameError(media._clean_error(exc)) from None
    if not stream.duration or not stream.fps:
        _measure(stream)
    return stream


def clock(seconds: float, precise: bool = True) -> str:
    """1:23.4, or 1:02:03.4 past an hour - where a frame is, to the tenth."""
    seconds = max(0.0, seconds or 0.0)
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    text = f'{hours}:{minutes:02d}:{secs:02d}' if hours else f'{minutes}:{secs:02d}'
    if precise:
        text += f'.{int((seconds - whole) * 10)}'
    return text


def frame_filename(title: str, at: float, kind: str) -> str:
    """'Title @ 1m23.4s.png' - a clock's colons are not allowed in Windows names."""
    whole = int(max(0.0, at))
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    tenth = int((max(0.0, at) - whole) * 10)
    stamp = f'{minutes}m{secs:02d}.{tenth}s'
    if hours:
        stamp = f'{hours}h{minutes:02d}m{secs:02d}.{tenth}s'
    return f'{safe_filename(title, "frame", 150)} @ {stamp}.{kind}'


def _ffmpeg() -> str:
    ffmpeg = find_tool('ffmpeg')
    if not ffmpeg:
        raise FrameError('FFmpeg is needed to read a frame')
    return ffmpeg


def _fetching(source: str, headers: dict | None) -> list:
    """How FFmpeg is to ask for a stream: with the headers the site wants."""
    if not source.startswith(('http://', 'https://')):
        return []
    args = []
    headers = {k: v for k, v in (headers or {}).items() if v}
    agent = next((headers.pop(k) for k in list(headers) if k.lower() == 'user-agent'), '')
    if agent:
        args += ['-user_agent', agent]
    if headers:
        args += ['-headers', ''.join(f'{k}: {v}\r\n' for k, v in headers.items())]
    # A stalled connection gives up rather than holding the slider forever.
    return args + ['-rw_timeout', '20000000']


def _input(source: str, headers: dict | None, at: float) -> list:
    """Seek first, then open: FFmpeg only fetches what it needs from there."""
    return ['-ss', f'{max(0.0, at):.3f}', *_fetching(source, headers), '-i', source]


def _measure(stream: Stream) -> None:
    """Fill in what the site did not say. A link straight to a video file comes
    with no length, and the slider cannot be drawn without one."""
    ffprobe = find_tool('ffprobe')
    if not ffprobe:
        return
    result = _run([ffprobe, '-v', 'error', *_fetching(stream.url, stream.headers),
                   '-select_streams', 'v:0', '-show_entries',
                   'format=duration:stream=width,height,avg_frame_rate', '-of', 'json',
                   '-i', stream.url], timeout=30)
    try:
        import json
        found = json.loads(result.stdout or b'{}')
    except ValueError:
        return
    video = (found.get('streams') or [{}])[0]
    try:
        stream.duration = stream.duration or float((found.get('format') or {}).get('duration') or 0)
    except ValueError:
        pass
    stream.width = stream.width or int(video.get('width') or 0)
    stream.height = stream.height or int(video.get('height') or 0)
    rate = str(video.get('avg_frame_rate') or '')
    if not stream.fps and '/' in rate:
        top, _, bottom = rate.partition('/')
        if bottom.strip('0') and top.isdigit() and bottom.isdigit():
            stream.fps = int(top) / int(bottom)


def _run(args: list, timeout: float = GRAB_TIMEOUT) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, capture_output=True, timeout=timeout,
                              creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        raise FrameError('Reading the frame took too long') from None
    except OSError as exc:
        raise FrameError(f'FFmpeg could not be started: {exc}') from None


def _complaint(result: subprocess.CompletedProcess) -> str:
    lines = (result.stderr or b'').decode('utf-8', 'replace').strip().splitlines()
    return lines[-1] if lines else 'FFmpeg found no frame there'


def preview(stream: Stream, at: float, width: int = 640) -> bytes:
    """A JPEG of the frame at `at`, no wider than `width`, for looking at."""
    result = _run([_ffmpeg(), '-hide_banner', '-v', 'error', '-nostdin',
                   *_input(stream.url, stream.headers, at),
                   '-frames:v', '1', '-an', '-sn', '-dn',
                   '-vf', f"scale='min({width},iw)':-2",
                   '-q:v', '4', '-f', 'image2pipe', '-c:v', 'mjpeg', 'pipe:1'])
    if result.returncode != 0 or not result.stdout:
        raise FrameError(_complaint(result))
    return result.stdout


def save(source: str, headers: dict | None, at: float, target: str) -> str:
    """Write the frame at `at` to `target`, full size; PNG or JPG by its name."""
    jpeg = target.lower().endswith(('.jpg', '.jpeg'))
    # Written under another name and renamed, so a half-written picture never
    # sits where the finished one should be.
    partial = target + '.part'
    result = _run([_ffmpeg(), '-hide_banner', '-v', 'error', '-nostdin', '-y',
                   *_input(source, headers, at), '-frames:v', '1', '-an', '-sn', '-dn',
                   # -q:v 2 is as good as JPEG usefully gets
                   *(['-c:v', 'mjpeg', '-q:v', '2'] if jpeg else ['-c:v', 'png']),
                   '-f', 'image2', '-update', '1', partial])
    if result.returncode != 0 or not os.path.exists(partial) or not os.path.getsize(partial):
        try:
            os.remove(partial)
        except OSError:
            pass
        raise FrameError(_complaint(result))
    os.replace(partial, target)
    return target


class FrameReader:
    """Reads frames to look at, one at a time, always the latest asked for.

    Moving the slider asks for many frames in a row, and only the last of them
    matters, so a request made while one is being read replaces any still
    waiting. The callbacks run on the reading thread; each interface hands
    them to its own.
    """

    def __init__(self, url: str, settings, on_frame, on_error, on_stream=None,
                 width: int = 640):
        self.url = url
        self.settings = settings
        self.width = width
        self.stream: Stream | None = None
        self._on_frame = on_frame          # (seconds, jpeg bytes)
        self._on_error = on_error          # (message)
        self._on_stream = on_stream        # (Stream), once it is found
        self._wanted: float | None = None
        self._lock = threading.Lock()
        self._busy = False
        self._closed = False

    def want(self, seconds: float) -> None:
        with self._lock:
            self._wanted = max(0.0, seconds)
            if self._busy or self._closed:
                return
            self._busy = True
        threading.Thread(target=self._work, name='grabbit-frames', daemon=True).start()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._wanted = None

    def _work(self) -> None:
        try:
            if self.stream is None:
                self.stream = find_stream(self.url, self.settings)
                if self._on_stream and not self._closed:
                    self._on_stream(self.stream)
            while True:
                with self._lock:
                    at, self._wanted = self._wanted, None
                    if at is None or self._closed:
                        self._busy = False
                        return
                if self.stream.duration:
                    at = min(at, max(0.0, self.stream.duration - 0.05))
                data = preview(self.stream, at, self.width)
                if not self._closed:
                    self._on_frame(at, data)
        except FrameError as exc:
            with self._lock:
                self._busy = False
                closed = self._closed
            if not closed:
                self._on_error(str(exc))
        except Exception as exc:                      # never silently
            log.exception('reading a frame failed')
            with self._lock:
                self._busy = False
            if not self._closed:
                self._on_error(f'Could not read that frame: {exc}')
