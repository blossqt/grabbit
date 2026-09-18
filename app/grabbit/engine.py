"""The download engine: owns aria2, the task list and the media jobs.

Threading: tasks are only ever touched on the GUI thread. A poller thread
reads aria2's status and hands snapshots over via a Qt signal; media jobs run
on their own threads and report through the same mechanism.
"""

import base64
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal

from . import media as media_mod
from .aria2rpc import Aria2Client, Aria2Error, Aria2Process
from .paths import find_tool, logs_dir, torrents_dir
from .tasks import (KIND_HTTP, KIND_IMAGE, KIND_MAGNET, KIND_MEDIA, KIND_TORRENT,
                    FINISHED_STATES, State, Task, TaskStore)
from .torrentmeta import parse_magnet, parse_torrent, select_file_spec
from .util import ipv6_available, safe_filename, send_to_recycle_bin, site_name, unique_path

log = logging.getLogger(__name__)

POLL_KEYS = ['gid', 'status', 'totalLength', 'completedLength', 'uploadLength',
             'downloadSpeed', 'uploadSpeed', 'connections', 'numSeeders', 'seeder',
             'errorCode', 'errorMessage', 'infoHash', 'followedBy', 'verifiedLength',
             'verifyIntegrityPending']

DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')


class Engine(QObject):
    tasks_updated = Signal(list)      # [task_id, ...] changed this tick
    task_added = Signal(str)
    task_removed = Signal(str)
    task_finished = Signal(str)       # finished successfully (for notifications)
    magnet_ready = Signal(str)        # torrent metadata arrived, ready to configure
    global_stats = Signal(dict)
    engine_message = Signal(str, str)  # level ('info'|'warning'|'error'), text

    _snapshot_ready = Signal(object)
    _media_event = Signal(str, str, object)
    _deferred_result = Signal(object, object)   # (callback, value) back on the GUI thread

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.store = TaskStore()
        self.store.load()
        self.process: Aria2Process | None = None
        self.client: Aria2Client | None = None
        self.running = False
        self.last_stats = {}

        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='grabbit')
        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(1000)
        self._poll_timer.timeout.connect(self._poll)
        self._save_timer = QTimer(self)
        self._save_timer.setInterval(3000)
        self._save_timer.timeout.connect(lambda: self.store.save())
        self._polling = False

        self._media_jobs: dict = {}        # task_id -> MediaJob
        self._media_queue: list = []
        self._media_gids: dict = {}        # aria2 gid -> task_id (parts of media jobs)

        self._snapshot_ready.connect(self._apply_snapshot)
        self._media_event.connect(self._on_media_event)
        self._deferred_result.connect(self._run_deferred)

    @staticmethod
    def _run_deferred(callback, value):
        try:
            callback(value)
        except Exception:
            log.exception('deferred callback failed')

    # ------------------------------------------------------------------ setup
    def start(self) -> bool:
        exe = find_tool('aria2c')
        if not exe:
            self.engine_message.emit('error', 'aria2c.exe was not found next to Grabbit.')
            return False
        self.process = Aria2Process(exe, self._aria2_options(), logs_dir() / 'aria2.log')
        try:
            self.client = self.process.start()
        except Aria2Error as exc:
            self.engine_message.emit('error', f'Could not start the download engine: {exc}')
            return False
        self.running = True
        self._restore_tasks()
        self._poll_timer.start()
        self._save_timer.start()
        return True

    def aria2_version(self) -> str:
        return self.process.version if self.process else ''

    def _aria2_options(self) -> dict:
        s = self.settings
        port = str(s.bt_port)
        options = {
            'dir': s.download_dir,
            'continue': 'true',
            'max-concurrent-downloads': str(max(1, s.max_active_downloads)),
            'max-connection-per-server': str(max(1, min(16, s.connections_per_server))),
            'split': str(max(1, min(64, s.connections_per_server))),
            'min-split-size': f'{max(1, s.min_split_size_mb)}M',
            'file-allocation': 'none',
            'disk-cache': '32M',
            'max-tries': '5',
            'retry-wait': '5',
            'connect-timeout': '30',
            'timeout': '60',
            'allow-overwrite': 'false',
            'auto-file-renaming': 'true',
            'content-disposition-default-utf8': 'true',
            'check-certificate': 'true',
            'max-download-result': '1000',
            'keep-unfinished-download-result': 'true',
            # Resume data stays close to current, so nothing meaningful is lost
            # when aria2 is told to exit without Grabbit waiting for it.
            'auto-save-interval': '15',
            'user-agent': s.user_agent or DEFAULT_UA,
            'bt-save-metadata': 'true',
            # Grabbit keeps its own .torrent copies and adds them explicitly.
            # Letting aria2 pick up a saved one turns a metadata-only magnet
            # into a full content download inside Grabbit's data folder.
            'bt-load-saved-metadata': 'false',
            'rpc-save-upload-metadata': 'false',
            'bt-detach-seed-only': 'true',
            'bt-max-peers': str(max(0, s.max_peers)),
            'enable-dht': 'true' if s.enable_dht else 'false',
            'dht-file-path': str(logs_dir().parent / 'dht.dat'),
            'dht-file-path6': str(logs_dir().parent / 'dht6.dat'),
            'listen-port': port,
            'dht-listen-port': port,
            'bt-enable-lpd': 'true' if s.enable_lpd else 'false',
            'enable-peer-exchange': 'true' if s.enable_pex else 'false',
            'bt-require-crypto': 'true' if s.require_encryption else 'false',
            'seed-ratio': str(max(0.0, s.seed_ratio)),
        }
        if s.enable_dht:
            # Without an entry point a fresh DHT routing table can never bootstrap.
            options['dht-entry-point'] = 'dht.transmissionbt.com:6881'
        use_ipv6 = ipv6_available() if s.ipv6_mode == 'auto' else s.ipv6_mode == 'on'
        if use_ipv6:
            options['enable-dht6'] = options['enable-dht']
            if s.enable_dht:
                options['dht-entry-point6'] = 'dht.libtorrent.org:25401'
        else:
            # Without a working IPv6 route, aria2 aborts tracker announces on the
            # first 'network unreachable' instead of falling back to IPv4.
            options['disable-ipv6'] = 'true'
        if not s.seed_after_download:
            options['seed-time'] = '0'
        elif s.seed_time_minutes > 0:
            options['seed-time'] = str(s.seed_time_minutes)
        if s.download_limit_kib > 0:
            options['max-overall-download-limit'] = f'{s.download_limit_kib}K'
        if s.upload_limit_kib > 0:
            options['max-overall-upload-limit'] = f'{s.upload_limit_kib}K'
        if s.proxy:
            options['all-proxy'] = s.proxy
        trackers = [t.strip() for t in (s.extra_trackers or '').splitlines() if t.strip()]
        if trackers:
            options['bt-tracker'] = ','.join(trackers)
        for line in (s.aria2_extra_options or '').splitlines():
            line = line.strip().lstrip('-')
            if line and '=' in line and not line.startswith('#'):
                key, _, value = line.partition('=')
                options[key.strip()] = value.strip()
        return options

    def apply_settings(self):
        """Push settings that aria2 can change without a restart."""
        if not self.client:
            return
        s = self.settings
        options = {
            'max-concurrent-downloads': str(max(1, s.max_active_downloads)),
            'max-overall-download-limit': f'{max(0, s.download_limit_kib)}K',
            'max-overall-upload-limit': f'{max(0, s.upload_limit_kib)}K',
            'max-connection-per-server': str(max(1, min(16, s.connections_per_server))),
            'split': str(max(1, min(64, s.connections_per_server))),
            'min-split-size': f'{max(1, s.min_split_size_mb)}M',
            'dir': s.download_dir,
        }
        self._submit(lambda: self.client.call('aria2.changeGlobalOption', options))
        self._pump_media()

    def shutdown(self, wait_for_aria2: bool = False):
        self.running = False
        self._poll_timer.stop()
        self._save_timer.stop()
        for job in list(self._media_jobs.values()):
            job.cancel()
        self.store.save(force=True)
        self._executor.shutdown(wait=False)
        if self.process:
            self.process.stop(wait=wait_for_aria2)
        self.process = None
        self.client = None

    def _submit(self, fn):
        if self.running or self.client:
            self._executor.submit(self._guard, fn)

    @staticmethod
    def _guard(fn):
        try:
            fn()
        except Aria2Error as exc:
            log.warning('aria2 call failed: %s', exc)
        except Exception:
            log.exception('engine task failed')

    # -------------------------------------------------------------- restoring
    def _restore_tasks(self):
        for task in self.store:
            try:
                if task.state in FINISHED_STATES:
                    continue
                if task.kind == KIND_MEDIA:
                    if task.state != State.PAUSED:
                        task.state = State.QUEUED
                        self._queue_media(task)
                elif task.kind in (KIND_HTTP, KIND_IMAGE):
                    self._add_uri_task(task, paused=task.state == State.PAUSED)
                elif task.kind == KIND_TORRENT and task.torrent_file and os.path.exists(task.torrent_file):
                    self._add_torrent_task(task, paused=task.state == State.PAUSED,
                                           seed_only=task.state == State.SEEDING)
                elif task.kind == KIND_MAGNET:
                    if task.torrent_file and os.path.exists(task.torrent_file):
                        # Metadata is already on disk - wait for the user to pick
                        # files rather than bothering the swarm again.
                        task.state = State.PAUSED
                        task.progress_note = 'Ready to start'
                    else:
                        self._add_magnet_task(task, paused=task.state == State.PAUSED)
            except Aria2Error as exc:
                task.state = State.ERROR
                task.error = str(exc)
        self.store.mark_dirty()

    # ---------------------------------------------------------------- adding
    def _register(self, task: Task) -> Task:
        self.store.add(task)
        self.task_added.emit(task.id)
        return task

    def add_direct(self, url: str, save_dir: str = '', name: str = '', headers=None,
                   site: str = '', kind: str = KIND_HTTP, thumbnail: str = '',
                   start: bool = True, mirrors=None, checksum: str = '') -> Task:
        save_dir = save_dir or self.settings.download_dir
        task = Task(kind=kind, source=url, save_dir=save_dir, site=site or site_name(url),
                    thumbnail=thumbnail, headers=list(headers or []),
                    mirrors=[m for m in (mirrors or []) if m and m != url], checksum=checksum)
        if name:
            task.name = safe_filename(name)
            task.out = task.name
        else:
            task.name = _name_from_url(url)
        task.state = State.QUEUED if start else State.PAUSED
        self._register(task)
        self._add_uri_task(task, paused=not start)
        self.store.mark_dirty()
        return task

    def _add_uri_task(self, task: Task, paused: bool = False):
        options = {
            'dir': task.save_dir,
            'gid': _gid_for(task),
            'continue': 'true',
        }
        if task.out:
            options['out'] = './' + task.out
        if task.headers:
            options['header'] = list(task.headers)
        if task.checksum:
            options['checksum'] = task.checksum
        if paused:
            options['pause'] = 'true'
        try:
            # Extra sources are mirrors of the same file; aria2 uses them together.
            task.gid = self.client.call('aria2.addUri', [task.source, *task.mirrors], options)
            task.error = ''
        except Aria2Error as exc:
            task.state = State.ERROR
            task.error = str(exc)

    def add_magnet(self, magnet: str, save_dir: str = '', start: bool = True) -> Task | None:
        info = parse_magnet(magnet)
        if not info.get('info_hash'):
            self.engine_message.emit('error', 'That magnet link has no usable BitTorrent hash.')
            return None
        existing = self.store.by_info_hash(info['info_hash'])
        if existing:
            self.engine_message.emit('warning', f'“{existing.name or "That torrent"}” is already in the list.')
            return None
        task = Task(kind=KIND_MAGNET, source=magnet, save_dir=save_dir or self.settings.download_dir,
                    name=info.get('name') or f'Magnet {info["info_hash"][:8]}',
                    info_hash=info['info_hash'], state=State.METADATA if start else State.PAUSED)
        self._register(task)

        # Already fetched this magnet's metadata before? Skip straight to the
        # file picker instead of asking the swarm again.
        cached = torrents_dir() / f'{info["info_hash"].lower()}.torrent'
        if cached.exists():
            task.torrent_file = str(cached)
            task.state = State.PAUSED
            task.progress_note = 'Ready to start'
            try:
                meta = parse_torrent(cached.read_bytes())
                task.name = meta.name or task.name
                task.total = meta.total_size
            except Exception:
                task.torrent_file = ''
            if task.torrent_file:
                self.store.mark_dirty()
                if start:
                    self.magnet_ready.emit(task.id)
                return task

        self._add_magnet_task(task, paused=not start)
        self.store.mark_dirty()
        return task

    def _add_magnet_task(self, task: Task, paused: bool = False):
        """Phase 1 of a magnet: fetch metadata only, save the .torrent privately."""
        options = {
            'dir': str(torrents_dir()),
            'gid': _gid_for(task),
            'bt-metadata-only': 'true',
            'bt-save-metadata': 'true',
            'bt-load-saved-metadata': 'false',   # never download content in here
            'follow-torrent': 'false',
        }
        if paused:
            options['pause'] = 'true'
        try:
            task.gid = self.client.call('aria2.addUri', [task.source], options)
            task.error = ''
        except Aria2Error as exc:
            task.state = State.ERROR
            task.error = str(exc)

    def add_torrent(self, data: bytes, save_dir: str = '', selected: list | None = None,
                    start: bool = True, source: str = '') -> Task | None:
        try:
            meta = parse_torrent(data)
        except Exception as exc:
            self.engine_message.emit('error', f'That torrent file could not be read: {exc}')
            return None
        existing = self.store.by_info_hash(meta.info_hash)
        if existing:
            self.engine_message.emit('warning', f'“{existing.name or meta.name}” is already in the list.')
            return None

        path = torrents_dir() / f'{meta.info_hash or int(time.time())}.torrent'
        try:
            path.write_bytes(data)
        except OSError as exc:
            self.engine_message.emit('error', f'Could not store the torrent file: {exc}')
            return None

        task = Task(kind=KIND_TORRENT, source=source or f'magnet:?xt=urn:btih:{meta.info_hash}',
                    name=meta.name, save_dir=save_dir or self.settings.download_dir,
                    info_hash=meta.info_hash, torrent_file=str(path),
                    total=meta.total_size, state=State.QUEUED if start else State.PAUSED)
        if selected is not None:
            task.select_files = select_file_spec(selected, len(meta.files))
            task.total = sum(f.length for f in meta.files
                             if not task.select_files or f.index in set(selected))
        self._register(task)
        self._add_torrent_task(task, paused=not start)
        self.store.mark_dirty()
        return task

    def _add_torrent_task(self, task: Task, paused: bool = False, seed_only: bool = False):
        try:
            data = open(task.torrent_file, 'rb').read()
        except OSError as exc:
            task.state, task.error = State.ERROR, f'torrent file missing: {exc}'
            return
        options = {
            'dir': task.save_dir,
            'gid': _gid_for(task),
            'continue': 'true',
        }
        if task.select_files:
            options['select-file'] = task.select_files
        if paused:
            options['pause'] = 'true'
        if seed_only:
            options['bt-seed-unverified'] = 'true'
        try:
            task.gid = self.client.call('aria2.addTorrent',
                                        base64.b64encode(data).decode('ascii'), [], options)
            task.error = ''
        except Aria2Error as exc:
            task.state = State.ERROR
            task.error = str(exc)

    def start_torrent_from_metadata(self, task: Task, selected: list | None,
                                    save_dir: str = '', start: bool = True):
        """Second phase of a magnet: metadata is in, download the content."""
        if not task.torrent_file or not os.path.exists(task.torrent_file):
            task.state, task.error = State.ERROR, 'torrent metadata went missing'
            return
        try:
            meta = parse_torrent(open(task.torrent_file, 'rb').read())
        except Exception as exc:
            task.state, task.error = State.ERROR, f'unreadable metadata: {exc}'
            return
        task.kind = KIND_TORRENT
        task.name = meta.name or task.name
        task.save_dir = save_dir or task.save_dir
        task.total = meta.total_size
        if selected is not None:
            task.select_files = select_file_spec(selected, len(meta.files))
            chosen = set(selected)
            task.total = sum(f.length for f in meta.files if not task.select_files or f.index in chosen)
        task.state = State.QUEUED if start else State.PAUSED
        task.done = 0
        self._add_torrent_task(task, paused=not start)
        self.store.mark_dirty()
        self.tasks_updated.emit([task.id])

    def add_media(self, item, probe_result, quality: str = '', container: str = '',
                  save_dir: str = '', start: bool = True) -> Task:
        """Queue a video/audio item for yt-dlp."""
        save_dir = save_dir or self.settings.download_dir
        if self.settings.site_subfolders and probe_result.site:
            save_dir = os.path.join(save_dir, safe_filename(probe_result.site, 'site', 60))
        task = Task(kind=KIND_MEDIA, source=item.url or probe_result.url, save_dir=save_dir,
                    name=item.title or probe_result.title,
                    site=probe_result.site or site_name(probe_result.url),
                    thumbnail=item.thumbnail or probe_result.thumbnail)
        task.total = item.filesize or 0
        task.media = {
            'quality': quality or self.settings.video_quality,
            'container': container or self.settings.video_container,
            'info': item.info,
            'info_time': time.time() if item.info else 0,
            'parent_url': probe_result.url if len(probe_result.items) > 1 else '',
            'entry_id': item.key,
            'entry_index': item.index,
            'duration': item.duration,
        }
        task.state = State.QUEUED if start else State.PAUSED
        self._register(task)
        if start:
            self._queue_media(task)
        self.store.mark_dirty()
        return task

    def add_image(self, item, probe_result, save_dir: str = '', start: bool = True) -> Task:
        """Queue a photo (carousel slide / slideshow frame) as a direct download."""
        save_dir = save_dir or self.settings.download_dir
        if self.settings.site_subfolders and probe_result.site:
            save_dir = os.path.join(save_dir, safe_filename(probe_result.site, 'site', 60))
        os.makedirs(save_dir, exist_ok=True)
        ext = item.ext or 'jpg'
        if getattr(item, 'filename', ''):
            base = safe_filename(item.filename)          # the site's own name
        else:
            owner = probe_result.uploader or probe_result.site or 'post'
            post_id = _post_id(probe_result, item)
            base = safe_filename(f'{owner} - {post_id} - {item.index:02d}.{ext}')
        name = os.path.basename(unique_path(save_dir, base))
        headers = [f'{k}: {v}' for k, v in (item.headers or {}).items() if v]
        task = self.add_direct(item.direct_url, save_dir=save_dir, name=name, headers=headers,
                               site=probe_result.site, kind=KIND_IMAGE,
                               thumbnail=item.thumbnail or item.preview, start=start)
        task.total = item.filesize or 0
        return task

    # ------------------------------------------------------------- media jobs
    def _queue_media(self, task: Task):
        if task.id not in self._media_queue and task.id not in self._media_jobs:
            self._media_queue.append(task.id)
        self._pump_media()

    def _pump_media(self):
        limit = max(1, self.settings.max_media_jobs)
        while self._media_queue and len(self._media_jobs) < limit:
            task_id = self._media_queue.pop(0)
            task = self.store.get(task_id)
            if not task or task.state in FINISHED_STATES or task.state == State.PAUSED:
                continue
            job = media_mod.MediaJob(task, self.settings, {
                'emit': lambda tid, event, payload: self._media_event.emit(tid, event, payload),
                'aria2_add': self._media_aria2_add,
                'aria2_status': self._media_aria2_status,
                'aria2_control': self._media_aria2_control,
            })
            self._media_jobs[task_id] = job
            task.state = State.EXTRACTING
            task.error = ''
            self.tasks_updated.emit([task_id])
            job.start()

    def _media_aria2_add(self, task_id: str, url: str, options: dict) -> str:
        gid = self.client.call('aria2.addUri', [url], options)
        self._media_gids[gid] = task_id
        return gid

    def _media_aria2_status(self, gid: str) -> dict:
        return self.client.call('aria2.tellStatus', gid,
                                ['gid', 'status', 'totalLength', 'completedLength',
                                 'downloadSpeed', 'errorMessage', 'errorCode'])

    def _media_aria2_control(self, gid: str, action: str):
        try:
            if action == 'pause':
                self.client.call('aria2.forcePause', gid)
            elif action == 'unpause':
                self.client.call('aria2.unpause', gid)
            elif action == 'remove':
                self.client.call('aria2.forceRemove', gid)
                self.client.call('aria2.removeDownloadResult', gid)
                self._media_gids.pop(gid, None)
            elif action == 'forget':
                self.client.call('aria2.removeDownloadResult', gid)
                self._media_gids.pop(gid, None)
        except Aria2Error:
            self._media_gids.pop(gid, None)

    def _on_media_event(self, task_id: str, event: str, payload: object):
        task = self.store.get(task_id)
        if not task:
            return
        payload = payload or {}
        if event == 'progress':
            task.done = payload.get('done', task.done)
            task.total = payload.get('total', task.total) or task.total
            task.down_speed = int(payload.get('speed') or 0)
            task.eta = payload.get('eta')
            task.progress_note = payload.get('note', '')
            if task.state != State.DOWNLOADING:
                task.state = State.DOWNLOADING
        elif event == 'state':
            task.state = payload.get('state', task.state)
            task.progress_note = payload.get('note', '')
            task.down_speed = 0
        elif event == 'log':
            task.add_log(payload.get('message', ''))
        elif event == 'finished':
            task.state = State.COMPLETED
            task.file_path = payload.get('filepath', '')
            task.name = os.path.basename(task.file_path) or task.name
            size = payload.get('size') or 0
            if size:
                task.total = task.done = size
            else:
                task.done = task.total
            task.completed_at = time.time()
            task.down_speed = 0
            task.progress_note = ''
            self._finish_media(task_id)
            self.task_finished.emit(task_id)
        elif event == 'error':
            task.state = State.ERROR
            task.error = payload.get('message', 'download failed')
            task.add_log(f'Error: {task.error}')
            task.down_speed = 0
            self._finish_media(task_id)
        elif event == 'paused':
            task.state = State.PAUSED
            task.down_speed = 0
            self._finish_media(task_id)
        elif event == 'cancelled':
            self._finish_media(task_id)
            return
        self.store.mark_dirty()
        self.tasks_updated.emit([task_id])

    def _finish_media(self, task_id: str):
        self._media_jobs.pop(task_id, None)
        self._media_gids = {g: t for g, t in self._media_gids.items() if t != task_id}
        self._pump_media()

    # ---------------------------------------------------------------- polling
    def _poll(self):
        if not self.client or self._polling:
            return
        self._polling = True

        def work():
            try:
                results = self.client.multicall([
                    ('aria2.tellActive', [POLL_KEYS]),
                    ('aria2.tellWaiting', [0, 500, POLL_KEYS]),
                    ('aria2.tellStopped', [0, 500, POLL_KEYS]),
                    ('aria2.getGlobalStat', []),
                ])
            except Aria2Error as exc:
                self._snapshot_ready.emit({'error': str(exc)})
                return
            statuses = []
            for group in results[:3]:
                if isinstance(group, list):
                    statuses.extend(group)
            stats = results[3] if isinstance(results[3], dict) else {}
            self._snapshot_ready.emit({'statuses': statuses, 'stats': stats})

        self._executor.submit(self._guard, work)

    def _apply_snapshot(self, snapshot: dict):
        self._polling = False
        if snapshot.get('error'):
            if self.running and self.process and not self.process.is_running():
                self.running = False
                self._poll_timer.stop()
                self.engine_message.emit('error', 'The aria2 engine stopped unexpectedly.')
            return

        changed = []
        finished = []
        for status in snapshot.get('statuses') or []:
            gid = status.get('gid')
            if not gid or gid in self._media_gids:
                continue
            task = self.store.by_gid(gid)
            if not task:
                continue
            if self._update_task(task, status, finished):
                changed.append(task.id)

        stats = snapshot.get('stats') or {}
        if stats:
            self.last_stats = {
                'download_speed': int(stats.get('downloadSpeed') or 0),
                'upload_speed': int(stats.get('uploadSpeed') or 0),
                'active': int(stats.get('numActive') or 0),
                'waiting': int(stats.get('numWaiting') or 0),
                'stopped': int(stats.get('numStopped') or 0),
            }
            self.global_stats.emit(self.last_stats)
        if changed:
            self.store.mark_dirty()
            self.tasks_updated.emit(changed)
        for task_id in finished:
            self.task_finished.emit(task_id)

    def _update_task(self, task: Task, status: dict, finished: list) -> bool:
        state = status.get('status')
        task.total = int(status.get('totalLength') or 0) or task.total
        task.done = int(status.get('completedLength') or 0)
        task.down_speed = int(status.get('downloadSpeed') or 0)
        task.up_speed = int(status.get('uploadSpeed') or 0)
        task.connections = int(status.get('connections') or 0)
        task.seeds = int(status.get('numSeeders') or 0)
        task.uploaded = task.uploaded_base + int(status.get('uploadLength') or 0)
        if status.get('infoHash') and not task.info_hash:
            task.info_hash = status['infoHash']
        remaining = task.total - task.done
        task.eta = remaining / task.down_speed if task.down_speed and remaining > 0 else None

        previous = task.state
        if state == 'active':
            if status.get('verifyIntegrityPending') or status.get('verifiedLength'):
                task.state = State.CHECKING
            elif task.kind == KIND_MAGNET:
                task.state = State.METADATA
            elif status.get('seeder') == 'true' and task.total and task.done >= task.total:
                task.state = State.SEEDING
                if not task.completed_at:
                    task.completed_at = time.time()
                    finished.append(task.id)
            else:
                task.state = State.DOWNLOADING
        elif state == 'waiting':
            task.state = State.QUEUED
        elif state == 'paused':
            task.state = State.PAUSED
            task.down_speed = task.up_speed = 0
        elif state == 'error':
            task.state = State.ERROR
            task.error = status.get('errorMessage') or f'aria2 error {status.get("errorCode")}'
            task.down_speed = task.up_speed = 0
            self._forget(task.gid)
        elif state == 'complete':
            task.down_speed = task.up_speed = 0
            if task.kind == KIND_MAGNET:
                self._on_metadata_ready(task)
            else:
                task.state = State.COMPLETED
                task.done = task.total or task.done
                if not task.completed_at:
                    task.completed_at = time.time()
                    finished.append(task.id)
                if not task.file_path:
                    self._resolve_file_path(task)
                self._forget(task.gid)
        elif state == 'removed':
            if task.state not in (State.PAUSED, State.COMPLETED):
                task.state = State.PAUSED
            self._forget(task.gid)
        if task.state != previous:
            task.progress_note = ''
        return True

    def _forget(self, gid: str):
        if gid:
            self._submit(lambda: self.client.call('aria2.removeDownloadResult', gid))

    def _resolve_file_path(self, task: Task):
        """Ask aria2 where the finished download actually landed."""
        def apply(files):
            paths = [f.get('path') for f in (files or []) if f.get('path')]
            if not paths:
                return
            task.file_path = paths[0] if len(paths) == 1 else (task.save_dir or paths[0])
            if task.is_torrent and task.name:
                folder = os.path.join(task.save_dir, task.name)
                task.file_path = folder if os.path.isdir(folder) else paths[0]
            elif not task.name:
                task.name = os.path.basename(paths[0])
            self.store.mark_dirty()
            self.tasks_updated.emit([task.id])

        self.fetch_files(task, apply)

    def _on_metadata_ready(self, task: Task):
        """A magnet's metadata arrived: stash the .torrent and tell the UI."""
        info_hash = (task.info_hash or '').lower()
        path = torrents_dir() / f'{info_hash}.torrent'
        if not path.exists():
            task.state = State.ERROR
            task.error = 'metadata download finished but no torrent file was written'
            return
        task.torrent_file = str(path)
        task.state = State.PAUSED
        task.progress_note = 'Ready to start'
        try:
            meta = parse_torrent(path.read_bytes())
            task.name = meta.name or task.name
            task.total = meta.total_size
        except Exception:
            pass
        self._forget(task.gid)
        task.gid = ''
        self.store.mark_dirty()
        self.magnet_ready.emit(task.id)

    # ------------------------------------------------------------- commands
    def pause(self, task_ids):
        changed = []
        for task_id in task_ids:
            task = self.store.get(task_id)
            if not task or task.state in (State.PAUSED, State.COMPLETED):
                continue
            if task.kind == KIND_MEDIA:
                job = self._media_jobs.get(task_id)
                if job:
                    job.pause()
                elif task_id in self._media_queue:
                    self._media_queue.remove(task_id)
                task.state = State.PAUSED
            elif task.gid:
                gid = task.gid
                self._submit(lambda g=gid: self.client.call('aria2.forcePause', g))
                task.state = State.PAUSED
            task.down_speed = task.up_speed = 0
            changed.append(task_id)
        if changed:
            self.store.mark_dirty()
            self.tasks_updated.emit(changed)

    def resume(self, task_ids):
        changed = []
        for task_id in task_ids:
            task = self.store.get(task_id)
            if not task or task.state not in (State.PAUSED, State.ERROR):
                continue
            task.error = ''
            if task.kind == KIND_MEDIA:
                job = self._media_jobs.get(task_id)
                if job:
                    job.resume()
                    task.state = State.DOWNLOADING
                else:
                    task.state = State.QUEUED
                    self._queue_media(task)
            elif task.kind == KIND_MAGNET and task.torrent_file:
                self.magnet_ready.emit(task_id)
                continue
            elif task.gid and self._gid_known(task.gid):
                gid = task.gid
                self._submit(lambda g=gid: self.client.call('aria2.unpause', g))
                task.state = State.QUEUED
            else:
                task.state = State.QUEUED
                if task.kind == KIND_TORRENT:
                    self._add_torrent_task(task)
                elif task.kind == KIND_MAGNET:
                    self._add_magnet_task(task)
                else:
                    self._add_uri_task(task)
            changed.append(task_id)
        if changed:
            self.store.mark_dirty()
            self.tasks_updated.emit(changed)

    def _gid_known(self, gid: str) -> bool:
        try:
            self.client.call('aria2.tellStatus', gid, ['gid'])
            return True
        except Aria2Error:
            return False

    def remove(self, task_ids, delete_files: bool = False):
        for task_id in list(task_ids):
            task = self.store.get(task_id)
            if not task:
                continue
            job = self._media_jobs.get(task_id)
            if job:
                job.cancel()
            if task_id in self._media_queue:
                self._media_queue.remove(task_id)
            paths = self._task_paths(task) if delete_files else []
            gid = task.gid
            if gid:
                self._submit(lambda g=gid: self._remove_gid(g))
            self.store.remove(task_id)
            self.task_removed.emit(task_id)
            if delete_files and paths:
                self._submit(lambda p=paths: self._delete_paths(p))
        self.store.save(force=True)
        self._pump_media()

    def _remove_gid(self, gid: str):
        try:
            self.client.call('aria2.forceRemove', gid)
        except Aria2Error:
            pass
        try:
            self.client.call('aria2.removeDownloadResult', gid)
        except Aria2Error:
            pass

    def _task_paths(self, task: Task) -> list:
        paths = []
        if task.file_path:
            paths.append(task.file_path)
        if task.gid:
            try:
                for entry in self.client.call('aria2.getFiles', task.gid) or []:
                    if entry.get('path'):
                        paths.append(entry['path'])
            except Aria2Error:
                pass
        if task.out and task.save_dir:
            paths.append(os.path.join(task.save_dir, task.out))
        if task.is_torrent and task.name and task.save_dir:
            paths.append(os.path.join(task.save_dir, task.name))
        result = []
        for path in paths:
            if path and path not in result:
                result.append(path)
                if os.path.exists(path + '.aria2'):
                    result.append(path + '.aria2')
        return result

    @staticmethod
    def _delete_paths(paths):
        time.sleep(0.6)  # let aria2 close its handles first
        send_to_recycle_bin(paths)

    def set_speed_limits(self, download_kib: int, upload_kib: int):
        self.settings.download_limit_kib = max(0, int(download_kib))
        self.settings.upload_limit_kib = max(0, int(upload_kib))
        self.settings.save()
        self.apply_settings()

    # ------------------------------------------------------- detail queries
    def fetch_files(self, task: Task, callback):
        """aria2's per-file list for the Files tab (async)."""
        if not task.gid or not self.client:
            callback([])
            return

        def work():
            try:
                files = self.client.call('aria2.getFiles', task.gid) or []
            except Aria2Error:
                files = []
            self._deferred_result.emit(callback, files)

        self._submit(work)

    def fetch_peers(self, task: Task, callback):
        if not task.gid or not task.is_torrent or not self.client:
            callback([])
            return

        def work():
            try:
                peers = self.client.call('aria2.getPeers', task.gid) or []
            except Aria2Error:
                peers = []
            self._deferred_result.emit(callback, peers)

        self._submit(work)

    def fetch_status(self, task: Task, keys: list, callback):
        if not task.gid or not self.client:
            callback({})
            return

        def work():
            try:
                status = self.client.call('aria2.tellStatus', task.gid, keys) or {}
            except Aria2Error:
                status = {}
            self._deferred_result.emit(callback, status)

        self._submit(work)


def _gid_for(task: Task) -> str:
    """Stable 16-hex-character aria2 gid derived from the task id."""
    gid = ''.join(c for c in task.id.lower() if c in '0123456789abcdef')
    return (gid + '0' * 16)[:16]


def _post_id(probe_result, item) -> str:
    """Identify the post a photo came from, for its file name."""
    from urllib.parse import urlparse
    parts = [p for p in urlparse(probe_result.url or '').path.split('/') if p]
    for marker in ('p', 'reel', 'reels', 'tv', 'photo', 'video', 'status'):
        if marker in parts:
            index = parts.index(marker)
            if index + 1 < len(parts):
                return parts[index + 1]
    if parts:
        return parts[-1]
    return (item.key or 'post').split('_')[0]


def _name_from_url(url: str) -> str:
    from urllib.parse import unquote, urlparse
    name = os.path.basename(unquote(urlparse(url).path or '')).strip()
    return safe_filename(name) if name else (site_name(url) or 'download')
