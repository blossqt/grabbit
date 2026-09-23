"""How the window and the downloader talk: a line of JSON each way.

The downloader runs in a process of its own (service.py, host.py), so that
closing the window leaves the downloads running, and the window asks it for
everything over a socket on the phone itself (remote.py). This is what the two
ends share: where to find each other, and how requests, answers, downloads and
read links are written down.

The socket is TCP on 127.0.0.1, as aria2's own is, and like aria2's it takes a
secret: any app on the phone can reach a local port, but only this one can
read the file the secret is kept in.
"""

import base64
import dataclasses
import json
import os
import secrets
import socket

from grabbit.analyze import Analysis
from grabbit.mediaitems import MediaItem, ProbeResult
from grabbit.metalink import MetalinkFile
from grabbit.tasks import Task
from grabbit.torrentmeta import TorrentFile, TorrentInfo

from . import paths

# What a snapshot leaves out of a download, being large and never drawn:
# yt-dlp's whole reading of a video, the headers and mirrors a file is
# fetched with, and the log - which the details sheet asks for by itself
# (fetch_log) while it shows it.
HEAVY = frozenset({'media', 'log', 'headers', 'mirrors'})
# What the window does read from a download's media details.
LIGHT_MEDIA = ('quality', 'container', 'frame_at', 'frame_format')
TASK_FIELDS = tuple(f.name for f in dataclasses.fields(Task) if f.name not in HEAVY)

# The only kinds of object a read link is made of - and so the only ones a
# message may bring to life at the other end.
KINDS = {cls.__name__: cls for cls in (Analysis, ProbeResult, MediaItem, MetalinkFile,
                                       TorrentInfo, TorrentFile)}

# A line longer than this is not one of ours.
LONGEST = 64 * 1024 * 1024


def address_file():
    return paths.data_dir() / 'engine.json'


def new_secret() -> str:
    return secrets.token_hex(16)


def publish(port: int, secret: str, boot: str) -> None:
    """Say where the downloader listens - for the window, and no one else."""
    path = address_file()
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps({'port': port, 'secret': secret, 'boot': boot,
                               'pid': os.getpid()}), encoding='utf-8')
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, path)


def published() -> dict | None:
    try:
        found = json.loads(address_file().read_text(encoding='utf-8'))
        return found if found.get('port') and found.get('secret') else None
    except (OSError, ValueError):
        return None


def withdraw(boot: str) -> None:
    """Take the address down, if it is still this downloader's."""
    found = published()
    if found and found.get('boot') == boot:
        try:
            address_file().unlink()
        except OSError:
            pass


# ------------------------------------------------------------- framing
def send(sock: socket.socket, message: dict) -> None:
    sock.sendall(json.dumps(message, separators=(',', ':'), default=str).encode('utf-8') + b'\n')


class Reader:
    """Whole lines off a socket, however the bytes arrive."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.buffer = b''

    def read(self) -> dict | None:
        """The next message, or None once the other end has gone."""
        while b'\n' not in self.buffer:
            chunk = self.sock.recv(262144)
            if not chunk:
                return None
            self.buffer += chunk
            if len(self.buffer) > LONGEST:
                raise ValueError('message too long')
        line, _, self.buffer = self.buffer.partition(b'\n')
        return json.loads(line.decode('utf-8'))


# ----------------------------------------------------- read links, sent over
def encode(value):
    """A read link - dataclasses, bytes and all - as plain JSON values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {'__kind__': type(value).__name__,
                'fields': {f.name: encode(getattr(value, f.name))
                           for f in dataclasses.fields(value)}}
    if isinstance(value, (bytes, bytearray)):
        return {'__bytes__': base64.b64encode(bytes(value)).decode('ascii')}
    if isinstance(value, dict):
        return {str(key): encode(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode(item) for item in value]
    return value


def decode(value):
    if isinstance(value, dict):
        if '__bytes__' in value and len(value) == 1:
            return base64.b64decode(value['__bytes__'])
        if '__kind__' in value and set(value) == {'__kind__', 'fields'}:
            cls = KINDS.get(value['__kind__'])
            if cls is None:
                raise ValueError(f'unexpected {value["__kind__"]!r} in a message')
            known = {f.name for f in dataclasses.fields(cls)}
            return cls(**{key: decode(item) for key, item in value['fields'].items()
                          if key in known})
        return {key: decode(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode(item) for item in value]
    return value


# ---------------------------------------------------- downloads, drawn over
def task_state(task: Task) -> dict:
    """What the window draws of a download."""
    state = {name: getattr(task, name) for name in TASK_FIELDS}
    media = task.media or {}
    state['media'] = {key: media[key] for key in LIGHT_MEDIA if key in media}
    return state


def apply_state(task: Task, state: dict) -> None:
    """Bring the window's copy of a download up to date, in place: the
    window holds on to the object, and asks whether it is still the same one."""
    for name, value in state.items():
        if name == 'media':
            task.media = dict(value or {})
        elif name in TASK_FIELDS:
            setattr(task, name, value)
