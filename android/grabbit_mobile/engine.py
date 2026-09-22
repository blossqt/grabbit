"""The download engine, without Qt.

Everything below this file is shared with the desktop build unchanged: link
analysis, the yt-dlp integration and its Instagram/TikTok photo extractors,
aria2's RPC client, torrent and metalink parsing. Only the orchestration is
rewritten here, because the desktop version is built on Qt signals and timers
that do not exist on a phone.

This deliberately duplicates some of desktop engine.py while the phone build is
being proven. Once it is, the sensible move is to lift this loop into the
shared package and let the desktop keep a thin Qt adapter on top of it.
"""

import logging
import os
import shutil
import threading
import time

from grabbit import analyze as analyze_mod
from grabbit.aria2rpc import Aria2Error, Aria2Process
from grabbit.tasks import (KIND_HTTP, KIND_IMAGE, KIND_MAGNET, KIND_MEDIA, KIND_TORRENT,
                           FINISHED_STATES, State, Task, TaskStore, task_paths)
from grabbit.torrentmeta import parse_torrent, select_file_spec
from grabbit.util import human_size, ipv6_available, safe_filename

from . import paths

log = logging.getLogger(__name__)

POLL_KEYS = ['gid', 'status', 'totalLength', 'completedLength', 'uploadLength',
             'downloadSpeed', 'uploadSpeed', 'connections', 'numSeeders', 'seeder',
             'errorCode', 'errorMessage', 'infoHash']


class MobileEngine:
    """Runs aria2, keeps the task list, and reports changes through callbacks."""

    def __init__(self, settings, on_change=None, on_message=None):
        self.settings = settings
        self.store = TaskStore()
        self.store.load()
        self.on_change = on_change or (lambda: None)
        self.on_message = on_message or (lambda level, text: None)

        self.process: Aria2Process | None = None
        self.client = None
        self.running = False
        self.stats = {}

        self._poller: threading.Thread | None = None
        self._lock = threading.RLock()
        self._media_jobs: dict = {}
        self._media_queue: list = []
        self._media_gids: dict = {}

    # ------------------------------------------------------------------ start
    def start(self) -> bool:
        exe = paths.find_tool('aria2c')
        if not exe:
            self.on_message('error', 'aria2 is missing from this build')
            return False
        if not os.access(exe, os.X_OK):
            # Only a copy we made ourselves can be fixed up like this. When
            # aria2 is running from the native library directory it is already
            # executable, and belongs to the system rather than to us.
            try:
                os.chmod(exe, 0o755)
            except OSError as exc:
                self.on_message('error', f'aria2 cannot be run: {exc}')
                return False

        try:
            self.process = Aria2Process(exe, self._aria2_options(), paths.logs_dir() / 'aria2.log')
            self.client = self.process.start()
        except Aria2Error as exc:
            self.on_message('error', f'Could not start the download engine: {exc}')
            return False

        self.running = True
        self._restore()
        self._poller = threading.Thread(target=self._poll_loop, name='grabbit-poll', daemon=True)
        self._poller.start()
        return True

    def _aria2_options(self) -> dict:
        settings = self.settings
        port = str(getattr(settings, 'bt_port', 51413))
        options = {
            'dir': str(paths.downloads_dir()),
            'continue': 'true',
            'max-concurrent-downloads': str(max(1, getattr(settings, 'max_active_downloads', 3))),
            'max-connection-per-server': str(max(1, min(16, getattr(settings, 'connections_per_server', 8)))),
            'split': '8',
            'min-split-size': '1M',
            'file-allocation': 'none',
            'disk-cache': '16M',            # phones have less memory to spare
            'auto-save-interval': '15',
            'max-tries': '5',
            'retry-wait': '5',
            'connect-timeout': '30',
            'timeout': '60',
            'allow-overwrite': 'false',
            'auto-file-renaming': 'true',
            'content-disposition-default-utf8': 'true',
            'check-certificate': 'true',
            # Android has no /etc/resolv.conf for c-ares to read, and the only
            # resolver that knows about the current network is the system one.
            'async-dns': 'false',
            'bt-save-metadata': 'true',
            'bt-load-saved-metadata': 'false',
            'bt-detach-seed-only': 'true',
            'enable-dht': 'true',
            'dht-entry-point': 'dht.transmissionbt.com:6881',
            'dht-file-path': str(paths.data_dir() / 'dht.dat'),
            'listen-port': port,
            'dht-listen-port': port,
            'seed-ratio': '1.0',
            'max-download-result': '500',
        }
        # Android exposes no CA bundle that an OpenSSL build finds by itself,
        # so point aria2 at the one yt-dlp already ships.
        from .bootstrap import ca_bundle
        bundle = ca_bundle()
        if bundle:
            options['ca-certificate'] = bundle
        if not ipv6_available():
            options['disable-ipv6'] = 'true'
        return options

    def _restore(self):
        for task in self.store:
            if task.state in FINISHED_STATES:
                continue
            try:
                if task.kind == KIND_MEDIA:
                    if task.state != State.PAUSED:
                        task.state = State.QUEUED
                        self._queue_media(task)
                elif task.kind in (KIND_HTTP, KIND_IMAGE):
                    self._add_uri(task, paused=task.state == State.PAUSED)
                elif task.kind == KIND_TORRENT and task.torrent_file and os.path.exists(task.torrent_file):
                    self._add_torrent(task, paused=task.state == State.PAUSED)
                elif task.kind == KIND_MAGNET:
                    if task.torrent_file and os.path.exists(task.torrent_file):
                        task.state = State.PAUSED
                    else:
                        self._add_magnet(task, paused=task.state == State.PAUSED)
            except Aria2Error as exc:
                task.state, task.error = State.ERROR, str(exc)
        self.store.mark_dirty()

    # ----------------------------------------------------------------- adding
    def analyze_link(self, url: str, on_done) -> None:
        """Work out what a link is, off the interface thread.

        on_done(analysis) is called from the worker; nothing is queued until
        the choices it allows have been made (add_analysis). This needs no
        aria2, so a link can be read while the engine is still starting.
        """
        def work():
            try:
                result = analyze_mod.analyze(url.strip(), self.settings)
            except Exception as exc:
                result = analyze_mod.Analysis(url=url, kind=analyze_mod.KIND_ERROR,
                                              error=f'Could not read that link: {exc}')
            on_done(result)

        threading.Thread(target=work, name='grabbit-analyze', daemon=True).start()

    def add_analysis(self, result, choice: dict | None = None) -> None:
        """Queue everything behind a link that has been read, as chosen."""
        threading.Thread(target=self._add_analysis, args=(result, dict(choice or {})),
                         name='grabbit-add', daemon=True).start()

    def add_link(self, url: str, quality: str = ''):
        """Read a link and queue all of it, without asking: the device tests."""
        def work():
            try:
                result = analyze_mod.analyze(url.strip(), self.settings)
            except Exception as exc:
                self.on_message('error', f'Could not read that link: {exc}')
                return
            self._add_analysis(result, {'quality': quality})

        self.on_message('info', 'Reading the link…')
        threading.Thread(target=work, name='grabbit-analyze', daemon=True).start()

    def _add_analysis(self, result, choice: dict):
        url = result.url
        kind = result.kind
        try:
            if kind == analyze_mod.KIND_ERROR:
                if not choice.get('as_file'):
                    self.on_message('error', result.error or 'That link could not be read')
                    return
                # Asked for anyway: whatever the address answers, saved as a file.
                self.add_file(url, name=analyze_mod.name_from_url(url))
            elif kind == analyze_mod.KIND_MAGNET:
                self.add_magnet(url)
            elif kind == analyze_mod.KIND_TORRENT:
                self.add_torrent(result.torrent_data, source=url)
            elif kind == analyze_mod.KIND_METALINK:
                for entry in result.metalink_files:
                    self.add_file(entry.urls[0], name=entry.name,
                                  mirrors=entry.urls, checksum=entry.checksum)
            elif kind in (analyze_mod.KIND_GALLERY, analyze_mod.KIND_PLAYLIST,
                          analyze_mod.KIND_MEDIA):
                for item in result.probe.items:
                    if item.optional:
                        continue
                    if item.kind == 'image' and item.direct_url:
                        self.add_image(item, result.probe)
                    else:
                        self.add_media(item, result.probe, choice)
            else:
                self.add_file(url, name=result.filename)
            self.on_message('info', f'Added: {result.title[:60] or url[:60]}')
        except Exception as exc:
            log.exception('adding failed')
            self.on_message('error', f'Could not add that download: {exc}')
        self.on_change()

    def add_file(self, url, name='', headers=None, mirrors=None, checksum='',
                 kind=KIND_HTTP, thumbnail='') -> Task:
        task = Task(kind=kind, source=url, save_dir=str(paths.downloads_dir()),
                    headers=list(headers or []), thumbnail=thumbnail,
                    mirrors=[m for m in (mirrors or []) if m and m != url], checksum=checksum)
        task.name = safe_filename(name) if name else (url.rsplit('/', 1)[-1] or 'download')
        task.out = task.name
        with self._lock:
            self.store.add(task)
        self._add_uri(task)
        return task

    def add_image(self, item, probe) -> Task:
        owner = probe.uploader or probe.site or 'post'
        name = getattr(item, 'filename', '') or f'{owner} - {item.index:02d}.{item.ext or "jpg"}'
        headers = [f'{k}: {v}' for k, v in (item.headers or {}).items() if v]
        return self.add_file(item.direct_url, name=name, headers=headers,
                             kind=KIND_IMAGE, thumbnail=item.thumbnail)

    def add_media(self, item, probe, choice: dict | None = None) -> Task:
        """Queue a video or sound, as chosen: quality and container, and for
        one frame of it, the moment and the picture type (grabbit.frames)."""
        choice = choice or {}
        task = Task(kind=KIND_MEDIA, source=item.url or probe.url,
                    save_dir=str(paths.downloads_dir()),
                    name=item.title or probe.title, site=probe.site,
                    thumbnail=item.thumbnail or probe.thumbnail)
        task.media = {
            'quality': choice.get('quality') or self.settings.video_quality,
            'container': choice.get('container') or self.settings.video_container,
            'info': item.info,
            'info_time': time.time() if item.info else 0,
            'parent_url': probe.url if len(probe.items) > 1 else '',
            'entry_id': item.key,
            'entry_index': item.index,
            'duration': item.duration,
        }
        if task.media['quality'] == 'frame':
            from grabbit import frames
            task.media['frame_at'] = float(choice.get('frame_at') or 0)
            task.media['frame_format'] = choice.get('frame_format') or 'png'
            task.name = frames.frame_filename(task.name, task.media['frame_at'],
                                              task.media['frame_format'])
        with self._lock:
            self.store.add(task)
        self._queue_media(task)
        return task

    def add_magnet(self, magnet: str) -> Task | None:
        from grabbit.torrentmeta import parse_magnet
        info = parse_magnet(magnet)
        if not info.get('info_hash'):
            self.on_message('error', 'That magnet link has no usable hash')
            return None
        if self.store.by_info_hash(info['info_hash']):
            self.on_message('warning', 'That torrent is already in the list')
            return None
        task = Task(kind=KIND_MAGNET, source=magnet, save_dir=str(paths.downloads_dir()),
                    name=info.get('name') or f'Magnet {info["info_hash"][:8]}',
                    info_hash=info['info_hash'], state=State.METADATA)
        cached = paths.torrents_dir() / f'{info["info_hash"].lower()}.torrent'
        with self._lock:
            self.store.add(task)
        if cached.exists():
            task.torrent_file = str(cached)
            self.start_torrent_from_metadata(task)
        else:
            self._add_magnet(task)
        return task

    def add_torrent(self, data: bytes, selected=None, source: str = '') -> Task | None:
        try:
            meta = parse_torrent(data)
        except Exception as exc:
            self.on_message('error', f'That torrent could not be read: {exc}')
            return None
        if self.store.by_info_hash(meta.info_hash):
            self.on_message('warning', 'That torrent is already in the list')
            return None
        path = paths.torrents_dir() / f'{meta.info_hash}.torrent'
        path.write_bytes(data)
        task = Task(kind=KIND_TORRENT, source=source or f'magnet:?xt=urn:btih:{meta.info_hash}',
                    name=meta.name, save_dir=str(paths.downloads_dir()),
                    info_hash=meta.info_hash, torrent_file=str(path), total=meta.total_size)
        if selected is not None:
            task.select_files = select_file_spec(selected, len(meta.files))
        with self._lock:
            self.store.add(task)
        self._add_torrent(task)
        return task

    def start_torrent_from_metadata(self, task: Task, selected=None, paused: bool = False):
        try:
            meta = parse_torrent(open(task.torrent_file, 'rb').read())
        except Exception as exc:
            task.state, task.error = State.ERROR, f'unreadable metadata: {exc}'
            return
        task.kind = KIND_TORRENT
        task.name = meta.name or task.name
        task.total = meta.total_size
        if selected is not None:
            task.select_files = select_file_spec(selected, len(meta.files))
        task.state = State.PAUSED if paused else State.QUEUED
        self._add_torrent(task, paused=paused)

    # ------------------------------------------------------- aria2 plumbing
    def _gid(self, task: Task) -> str:
        gid = ''.join(c for c in task.id.lower() if c in '0123456789abcdef')
        return (gid + '0' * 16)[:16]

    def _add_uri(self, task: Task, paused: bool = False):
        options = {'dir': task.save_dir, 'gid': self._gid(task), 'continue': 'true'}
        if task.out:
            options['out'] = './' + task.out
        if task.headers:
            options['header'] = list(task.headers)
        if task.checksum:
            options['checksum'] = task.checksum
        if paused:
            options['pause'] = 'true'
        try:
            task.gid = self.client.call('aria2.addUri', [task.source, *task.mirrors], options)
            task.error = ''
        except Aria2Error as exc:
            task.state, task.error = State.ERROR, str(exc)

    def _add_magnet(self, task: Task, paused: bool = False):
        options = {
            'dir': str(paths.torrents_dir()), 'gid': self._gid(task),
            'bt-metadata-only': 'true', 'bt-save-metadata': 'true',
            'bt-load-saved-metadata': 'false', 'follow-torrent': 'false',
        }
        if paused:
            options['pause'] = 'true'
        try:
            task.gid = self.client.call('aria2.addUri', [task.source], options)
        except Aria2Error as exc:
            task.state, task.error = State.ERROR, str(exc)

    def _add_torrent(self, task: Task, paused: bool = False):
        import base64
        try:
            data = open(task.torrent_file, 'rb').read()
        except OSError as exc:
            task.state, task.error = State.ERROR, f'torrent file missing: {exc}'
            return
        options = {'dir': task.save_dir, 'gid': self._gid(task), 'continue': 'true'}
        if task.select_files:
            options['select-file'] = task.select_files
        if paused:
            options['pause'] = 'true'
        try:
            task.gid = self.client.call('aria2.addTorrent',
                                        base64.b64encode(data).decode('ascii'), [], options)
            task.error = ''
        except Aria2Error as exc:
            task.state, task.error = State.ERROR, str(exc)

    # ---------------------------------------------------------- media jobs
    def _queue_media(self, task: Task):
        if task.id not in self._media_queue and task.id not in self._media_jobs:
            self._media_queue.append(task.id)
        self._pump_media()

    def _pump_media(self):
        limit = max(1, getattr(self.settings, 'max_media_jobs', 2))
        while self._media_queue and len(self._media_jobs) < limit:
            task_id = self._media_queue.pop(0)
            task = self.store.get(task_id)
            if not task or task.state in FINISHED_STATES or task.state == State.PAUSED:
                continue
            # Here rather than at the top: it brings yt-dlp, which the app is
            # quicker to open without (main.py loads it just after).
            from grabbit.media import MediaJob
            job = MediaJob(task, self.settings, {
                'emit': self._on_media_event,
                'aria2_add': self._media_add,
                'aria2_status': self._media_status,
                'aria2_control': self._media_control,
            })
            self._media_jobs[task_id] = job
            task.state = State.EXTRACTING
            job.start()
        self.on_change()

    def _media_add(self, task_id: str, url: str, options: dict) -> str:
        gid = self.client.call('aria2.addUri', [url], options)
        self._media_gids[gid] = task_id
        return gid

    def _media_status(self, gid: str) -> dict:
        return self.client.call('aria2.tellStatus', gid, [
            'gid', 'status', 'totalLength', 'completedLength', 'downloadSpeed',
            'errorMessage', 'errorCode'])

    def _media_control(self, gid: str, action: str):
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

    def _on_media_event(self, task_id: str, event: str, payload):
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
            task.state = State.DOWNLOADING
        elif event == 'state':
            task.state = payload.get('state', task.state)
            task.progress_note = payload.get('note', '')
        elif event == 'log':
            task.add_log(payload.get('message', ''))
        elif event == 'finished':
            task.state = State.COMPLETED
            task.file_path = payload.get('filepath', '')
            task.name = os.path.basename(task.file_path) or task.name
            size = payload.get('size') or 0
            task.total = task.done = size or task.total
            task.completed_at = time.time()
            task.down_speed = 0
            task.progress_note = ''       # "Merging…" and the like are over
            self._finish_media(task_id)
        elif event == 'error':
            task.state = State.ERROR
            task.error = payload.get('message', 'download failed')
            task.down_speed = 0
            self._finish_media(task_id)
        elif event in ('paused', 'cancelled'):
            if event == 'paused':
                task.state = State.PAUSED
            self._finish_media(task_id)
        self.store.mark_dirty()
        self.on_change()

    def _finish_media(self, task_id: str):
        self._media_jobs.pop(task_id, None)
        self._media_gids = {g: t for g, t in self._media_gids.items() if t != task_id}
        self._pump_media()

    # -------------------------------------------------------------- polling
    def _poll_loop(self):
        while self.running:
            try:
                self._poll_once()
            except Aria2Error as exc:
                log.debug('poll failed: %s', exc)
            except Exception:
                log.exception('poll loop error')
            time.sleep(1.0)

    def _poll_once(self):
        results = self.client.multicall([
            ('aria2.tellActive', [POLL_KEYS]),
            ('aria2.tellWaiting', [0, 200, POLL_KEYS]),
            ('aria2.tellStopped', [0, 200, POLL_KEYS]),
            ('aria2.getGlobalStat', []),
        ])
        statuses = []
        for group in results[:3]:
            if isinstance(group, list):
                statuses.extend(group)
        if isinstance(results[3], dict):
            self.stats = {
                'download_speed': int(results[3].get('downloadSpeed') or 0),
                'upload_speed': int(results[3].get('uploadSpeed') or 0),
            }

        changed = False
        for status in statuses:
            gid = status.get('gid')
            if not gid or gid in self._media_gids:
                continue
            task = self.store.by_gid(gid)
            if task:
                self._apply(task, status)
                changed = True
        if changed:
            self.store.mark_dirty()
            self.store.save()
            self.on_change()

    def _apply(self, task: Task, status: dict):
        state = status.get('status')
        task.total = int(status.get('totalLength') or 0) or task.total
        task.done = int(status.get('completedLength') or 0)
        task.down_speed = int(status.get('downloadSpeed') or 0)
        task.up_speed = int(status.get('uploadSpeed') or 0)
        task.connections = int(status.get('connections') or 0)
        task.seeds = int(status.get('numSeeders') or 0)
        task.uploaded = task.uploaded_base + int(status.get('uploadLength') or 0)
        remaining = task.total - task.done
        task.eta = remaining / task.down_speed if task.down_speed and remaining > 0 else None

        if state == 'active':
            if task.kind == KIND_MAGNET:
                task.state = State.METADATA
            elif status.get('seeder') == 'true' and task.total and task.done >= task.total:
                task.state = State.SEEDING
            else:
                task.state = State.DOWNLOADING
        elif state == 'waiting':
            task.state = State.QUEUED
        elif state == 'paused':
            task.state = State.PAUSED
        elif state == 'error':
            task.state = State.ERROR
            task.error = status.get('errorMessage') or 'download failed'
            self._forget(task.gid)
        elif state == 'complete':
            if task.kind == KIND_MAGNET:
                self._metadata_ready(task)
            else:
                task.state = State.COMPLETED
                task.done = task.total or task.done
                task.completed_at = task.completed_at or time.time()
                if not task.file_path and task.out:
                    task.file_path = os.path.join(task.save_dir, task.out)
                self._forget(task.gid)

    def _metadata_ready(self, task: Task):
        path = paths.torrents_dir() / f'{(task.info_hash or "").lower()}.torrent'
        if not path.exists():
            task.state, task.error = State.ERROR, 'no torrent metadata was written'
            return
        task.torrent_file = str(path)
        self._forget(task.gid)
        task.gid = ''
        # Every file in it, but not yet. A torrent is usually the largest thing
        # anyone hands this app, and a phone is the worst place to find several
        # gigabytes arriving unasked - over mobile data, at that. The desktop
        # waits for a choice of files here; without a picker, waiting for a tap
        # is the same promise.
        self.start_torrent_from_metadata(task, paused=True)
        self.on_message('info', f'{task.name} ({human_size(task.total)}) - tap it to start')

    def _forget(self, gid: str):
        if gid:
            try:
                self.client.call('aria2.removeDownloadResult', gid)
            except Aria2Error:
                pass

    # ------------------------------------------------------------- commands
    def pause(self, task_ids):
        for task_id in task_ids:
            task = self.store.get(task_id)
            if not task or task.state in (State.PAUSED, State.COMPLETED):
                continue
            if task.kind == KIND_MEDIA:
                job = self._media_jobs.get(task_id)
                if job:
                    job.pause()
                task.state = State.PAUSED
            elif task.gid:
                try:
                    self.client.call('aria2.forcePause', task.gid)
                except Aria2Error:
                    pass
                task.state = State.PAUSED
        self.on_change()

    def resume(self, task_ids):
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
            elif task.gid:
                try:
                    self.client.call('aria2.unpause', task.gid)
                    task.state = State.QUEUED
                except Aria2Error:
                    task.state = State.QUEUED
                    self._add_uri(task)
        self.on_change()

    @staticmethod
    def _delete(path: str, inside: str) -> None:
        """Delete one thing a download left, if it is really inside the folder
        that download was writing to.

        A phone has no recycle bin to undo this with, and a multi-file torrent
        is a folder, so the containment check earns its place.
        """
        root = os.path.abspath(inside or '')
        target = os.path.abspath(path or '')
        if not root or not target.startswith(root + os.sep):
            log.warning('refusing to delete %s: outside %s', target, root)
            return
        try:
            if os.path.isdir(target):
                shutil.rmtree(target, ignore_errors=True)
            elif os.path.exists(target):
                os.remove(target)
        except OSError as exc:
            log.warning('could not delete %s: %s', target, exc)

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
            # Ask what is on disk while the download still exists to be asked.
            doomed = task_paths(task, self.client) if delete_files else []
            if task.gid:
                try:
                    self.client.call('aria2.forceRemove', task.gid)
                    self.client.call('aria2.removeDownloadResult', task.gid)
                except Aria2Error:
                    pass
            for path in doomed:
                self._delete(path, task.save_dir)
            self.store.remove(task_id)
        self.store.save(force=True)
        self._pump_media()
        self.on_change()

    # ------------------------------------------------- what the details ask
    def _query(self, method: str, *args, default=None, callback=None):
        """One aria2 call on a thread, with the answer handed back.

        The details panel asks for file lists, peers and tracker lists while
        the interface is drawing; none of that can happen on the thread that
        draws.
        """
        def work():
            try:
                result = self.client.call(method, *args)
            except (Aria2Error, AttributeError):
                result = default
            if callback:
                callback(result if result is not None else default)

        threading.Thread(target=work, name='grabbit-query', daemon=True).start()

    def fetch_files(self, task: Task, callback):
        """aria2's per-file list, for the Files tab."""
        if not task.gid or not self.client:
            callback([])
            return
        self._query('aria2.getFiles', task.gid, default=[], callback=callback)

    def fetch_peers(self, task: Task, callback):
        if not task.gid or not task.is_torrent or not self.client:
            callback([])
            return
        self._query('aria2.getPeers', task.gid, default=[], callback=callback)

    def fetch_status(self, task: Task, keys: list, callback):
        if not task.gid or not self.client:
            callback({})
            return
        self._query('aria2.tellStatus', task.gid, keys, default={}, callback=callback)

    def shutdown(self):
        self.running = False
        for job in list(self._media_jobs.values()):
            job.cancel()
        self.store.save(force=True)
        if self.process:
            self.process.stop(wait=False)
        self.process = None
