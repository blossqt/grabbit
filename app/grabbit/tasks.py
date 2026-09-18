"""The download list: one Task per row, persisted to tasks.json."""

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field, fields

from .paths import data_dir

log = logging.getLogger(__name__)


class State:
    QUEUED = 'queued'            # waiting for a free slot
    METADATA = 'metadata'        # fetching torrent metadata for a magnet
    EXTRACTING = 'extracting'    # asking yt-dlp for the real media URLs
    DOWNLOADING = 'downloading'
    PROCESSING = 'processing'    # merging / converting / embedding
    SEEDING = 'seeding'
    PAUSED = 'paused'
    COMPLETED = 'completed'
    ERROR = 'error'
    CHECKING = 'checking'


BUSY_STATES = {State.QUEUED, State.METADATA, State.EXTRACTING, State.DOWNLOADING,
               State.PROCESSING, State.SEEDING, State.CHECKING}
RUNNING_STATES = {State.METADATA, State.EXTRACTING, State.DOWNLOADING, State.PROCESSING,
                  State.SEEDING, State.CHECKING}
FINISHED_STATES = {State.COMPLETED, State.ERROR}

STATE_LABELS = {
    State.QUEUED: 'Queued',
    State.METADATA: 'Fetching metadata',
    State.EXTRACTING: 'Reading page',
    State.DOWNLOADING: 'Downloading',
    State.PROCESSING: 'Processing',
    State.SEEDING: 'Seeding',
    State.PAUSED: 'Paused',
    State.COMPLETED: 'Completed',
    State.ERROR: 'Error',
    State.CHECKING: 'Checking',
}

# Kinds
KIND_HTTP = 'http'        # plain file over http(s)/ftp/sftp
KIND_MAGNET = 'magnet'    # magnet link, before its metadata arrives
KIND_TORRENT = 'torrent'
KIND_MEDIA = 'media'      # anything yt-dlp extracts (video/audio)
KIND_IMAGE = 'image'      # a photo from a carousel/slideshow

_TRANSIENT = {'down_speed', 'up_speed', 'seeds', 'peers', 'connections', 'eta', 'progress_note'}


@dataclass
class Task:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    kind: str = KIND_HTTP
    source: str = ''             # url / magnet / original page url
    name: str = ''
    save_dir: str = ''
    state: str = State.QUEUED
    added_at: float = field(default_factory=time.time)
    completed_at: float = 0.0
    total: int = 0
    done: int = 0
    uploaded_base: int = 0       # bytes uploaded in previous sessions
    uploaded: int = 0
    error: str = ''
    gid: str = ''                # current aria2 download id
    info_hash: str = ''
    torrent_file: str = ''       # our own copy of the .torrent
    select_files: str = ''       # aria2 --select-file spec
    out: str = ''                # fixed output filename (http downloads)
    mirrors: list = field(default_factory=list)   # extra sources (metalink)
    checksum: str = ''           # 'sha-256=...' for aria2 to verify
    headers: list = field(default_factory=list)   # 'Name: value' strings
    site: str = ''
    thumbnail: str = ''
    media: dict = field(default_factory=dict)     # yt-dlp job details
    file_path: str = ''          # final file on disk
    log: list = field(default_factory=list)

    # transient, refreshed from aria2 on every poll
    down_speed: int = 0
    up_speed: int = 0
    seeds: int = 0
    peers: int = 0
    connections: int = 0
    eta: float | None = None
    progress_note: str = ''

    # ---------------------------------------------------------------- helpers
    @property
    def progress(self) -> float:
        if self.state == State.COMPLETED and not self.total:
            return 1.0
        if self.total <= 0:
            return 0.0
        return min(1.0, self.done / self.total)

    @property
    def ratio(self) -> float:
        if self.total <= 0 or self.kind not in (KIND_TORRENT, KIND_MAGNET):
            return 0.0
        return self.uploaded / self.total

    @property
    def is_torrent(self) -> bool:
        return self.kind in (KIND_TORRENT, KIND_MAGNET)

    @property
    def status_text(self) -> str:
        if self.state == State.ERROR and self.error:
            return f'Error: {self.error}'
        if self.progress_note:
            return self.progress_note
        return STATE_LABELS.get(self.state, self.state.title())

    def add_log(self, message: str):
        stamp = time.strftime('%H:%M:%S')
        self.log.append(f'{stamp}  {message}')
        del self.log[:-300]

    def to_json(self) -> dict:
        data = asdict(self)
        for key in _TRANSIENT:
            data.pop(key, None)
        data['log'] = self.log[-60:]
        return data

    @classmethod
    def from_json(cls, data: dict) -> 'Task':
        known = {f.name for f in fields(cls)}
        task = cls(**{k: v for k, v in data.items() if k in known})
        # Never restore a half-finished runtime state as if it were live.
        if task.state in (State.DOWNLOADING, State.PROCESSING, State.EXTRACTING, State.CHECKING):
            task.state = State.QUEUED
        return task


class TaskStore:
    """Ordered collection of tasks with throttled JSON persistence."""

    def __init__(self):
        self._tasks: dict[str, Task] = {}
        self._dirty = False
        self._last_save = 0.0

    @property
    def path(self):
        return data_dir() / 'tasks.json'

    def load(self):
        try:
            raw = json.loads(self.path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return
        for entry in raw.get('tasks', []):
            try:
                task = Task.from_json(entry)
                self._tasks[task.id] = task
            except (TypeError, ValueError) as exc:
                log.warning('skipping unreadable task: %s', exc)

    def save(self, force: bool = False):
        if not (self._dirty or force):
            return
        if not force and time.time() - self._last_save < 2:
            return
        payload = {'version': 1, 'tasks': [t.to_json() for t in self._tasks.values()]}
        tmp = self.path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(payload, indent=1), encoding='utf-8')
            os.replace(tmp, self.path)
            self._dirty = False
            self._last_save = time.time()
        except OSError as exc:
            log.error('could not save task list: %s', exc)

    def mark_dirty(self):
        self._dirty = True

    def add(self, task: Task) -> Task:
        self._tasks[task.id] = task
        self._dirty = True
        return task

    def remove(self, task_id: str) -> Task | None:
        task = self._tasks.pop(task_id, None)
        self._dirty = True
        return task

    def get(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    def by_gid(self, gid: str) -> Task | None:
        if not gid:
            return None
        for task in self._tasks.values():
            if task.gid == gid:
                return task
        return None

    def by_info_hash(self, info_hash: str) -> Task | None:
        if not info_hash:
            return None
        for task in self._tasks.values():
            if task.info_hash and task.info_hash.lower() == info_hash.lower():
                return task
        return None

    def find_source(self, source: str) -> Task | None:
        for task in self._tasks.values():
            if task.source == source and task.state not in FINISHED_STATES:
                return task
        return None

    def values(self):
        return list(self._tasks.values())

    def __len__(self):
        return len(self._tasks)

    def __iter__(self):
        return iter(list(self._tasks.values()))


def task_paths(task: Task, client=None) -> list:
    """Everything a task may have put on disk, aria2's control files included.

    Worth being thorough about, because this is what "delete the file too"
    acts on. A torrent is only named by ``task.name`` until it finishes, a
    multi-file one is a folder rather than a file, and aria2 leaves a
    ``.aria2`` beside whatever it was writing. When the download still exists,
    aria2 itself is the most reliable answer, so ask it first.

    Call this *before* removing the download: afterwards there is nothing left
    to ask.
    """
    found = []
    if task.file_path:
        found.append(task.file_path)
    if client is not None and task.gid:
        try:
            for entry in client.call('aria2.getFiles', task.gid) or []:
                if entry.get('path'):
                    found.append(entry['path'])
        except Exception:                      # the download may be gone already
            log.debug('aria2 could not list files for %s', task.gid)
    if task.out and task.save_dir:
        found.append(os.path.join(task.save_dir, task.out))
    if task.is_torrent and task.name and task.save_dir:
        found.append(os.path.join(task.save_dir, task.name))
    if task.info_hash and task.save_dir:
        # aria2 saves the metadata it fetched next to the download.
        found.append(os.path.join(task.save_dir, f'{task.info_hash.lower()}.torrent'))

    result = []
    for path in found:
        if path and path not in result:
            result.append(path)
            if os.path.exists(path + '.aria2'):
                result.append(path + '.aria2')
    return result
