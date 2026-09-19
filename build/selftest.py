"""End-to-end checks for Grabbit's engine, without the GUI.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\selftest.py --all

Each check downloads a small amount of real data into a temporary folder and
deletes it afterwards. Run a subset with e.g. --http --youtube.
"""

import argparse
import os
import shutil
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))

# Titles come from whatever people called their videos, and a Windows console
# is not UTF-8 by default. Printing one must never be what fails a check.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding='utf-8', errors='replace')
    except (AttributeError, ValueError, OSError):
        pass

from grabbit import media as media_mod          # noqa: E402
from grabbit.aria2rpc import Aria2Process       # noqa: E402
from grabbit.paths import find_tool             # noqa: E402
from grabbit.settings import Settings           # noqa: E402
from grabbit.torrentmeta import parse_torrent, select_file_spec  # noqa: E402
from grabbit.util import human_size             # noqa: E402

HTTP_URL = 'https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip'
TORRENT_INDEX = 'https://releases.ubuntu.com/24.04/'
YOUTUBE_URL = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
INSTAGRAM_URL = 'https://www.instagram.com/nasa/p/DdZMsPElzSl/'
TIKTOK_PHOTO_URL = 'https://www.tiktok.com/@toota__74/photo/7534075278499400967'
TIKTOK_VIDEO_URL = 'https://www.tiktok.com/@jade.wood/video/7434103280294300960'

results = []


def report(name, ok, detail='', external=False):
    """external=True marks a check that depends on strangers (a torrent swarm
    choosing to send us data). Those are reported as SKIP rather than failing
    the suite, because nothing in Grabbit can make them happen."""
    label = 'PASS' if ok else ('SKIP' if external else 'FAIL')
    results.append((name, True if ok else (None if external else False)))
    print(f'  [{label}] {name}' + (f' - {detail}' if detail else ''), flush=True)


class Engine:
    """Bare aria2 process + client, enough for the engine-level checks."""

    def __init__(self, folder):
        exe = find_tool('aria2c')
        assert exe, 'aria2c.exe not found - run build_aria2.sh first'
        from grabbit.util import ipv6_available
        options = {
            'dir': folder, 'continue': 'true', 'file-allocation': 'none',
            'max-connection-per-server': '16', 'split': '16', 'min-split-size': '1M',
            'bt-save-metadata': 'true', 'seed-time': '0', 'listen-port': '51466',
            'dht-listen-port': '51466',
            # Same as the app: without an entry point a fresh DHT table cannot
            # bootstrap, leaving magnets dependent on the tracker alone.
            'dht-entry-point': 'dht.transmissionbt.com:6881',
            'dht-file-path': os.path.join(folder, 'dht.dat'),
        }
        if not ipv6_available():
            options['disable-ipv6'] = 'true'
        self.process = Aria2Process(exe, options)
        self.client = self.process.start()

    def wait(self, gid, until=('complete',), timeout=180, min_bytes=None, label=''):
        deadline, last = time.time() + timeout, 0
        while time.time() < deadline:
            status = self.client.call('aria2.tellStatus', gid, [
                'status', 'completedLength', 'totalLength', 'downloadSpeed',
                'errorMessage', 'connections', 'numSeeders'])
            done = int(status.get('completedLength') or 0)
            total = int(status.get('totalLength') or 0)
            if done != last:
                last = done
                print(f'      {label}{human_size(done)}/{human_size(total)} '
                      f'@ {human_size(int(status.get("downloadSpeed") or 0))}/s '
                      f'conns={status.get("connections")} seeds={status.get("numSeeders", "-")}',
                      end='\r', flush=True)
            if min_bytes and done >= min_bytes:
                print()
                return status
            if status.get('status') in until:
                print()
                return status
            if status.get('status') == 'error':
                print()
                raise RuntimeError(status.get('errorMessage') or 'aria2 error')
            time.sleep(0.5)
        print()
        raise TimeoutError(f'timed out waiting for {gid}')

    def stop(self):
        self.process.stop()


# --------------------------------------------------------------------------- checks

def check_http(folder):
    print('\n== plain HTTPS download (multi-connection) ==')
    engine = Engine(folder)
    try:
        gid = engine.client.call('aria2.addUri', [HTTP_URL], {'dir': folder, 'out': 'aria2.zip'})
        status = engine.wait(gid, label='')
        path = os.path.join(folder, 'aria2.zip')
        size = os.path.getsize(path)
        report('downloads over HTTPS', status['status'] == 'complete' and size > 1_000_000,
               f'{human_size(size)}')
        with open(path, 'rb') as handle:
            report('file contents look right', handle.read(2) == b'PK')
    finally:
        engine.stop()


def latest_ubuntu_torrent() -> str:
    """Newest desktop .iso.torrent - older ones get dropped from Ubuntu's tracker."""
    import re
    with urllib.request.urlopen(TORRENT_INDEX, timeout=30) as response:
        html = response.read().decode('utf-8', 'replace')
    names = sorted(set(re.findall(r'href="(ubuntu-[\d.]+-desktop-amd64\.iso\.torrent)"', html)),
                   key=lambda n: [int(p) for p in re.findall(r'\d+', n)])
    if not names:
        raise RuntimeError('no Ubuntu desktop torrent found')
    return TORRENT_INDEX + names[-1]


def check_torrent(folder):
    print('\n== torrent + magnet ==')
    torrent_path = os.path.join(folder, 'test.torrent')
    url = latest_ubuntu_torrent()
    print(f'   using {url.rsplit("/", 1)[-1]}')
    urllib.request.urlretrieve(url, torrent_path)
    meta = parse_torrent(open(torrent_path, 'rb').read())
    report('reads .torrent metadata', bool(meta.info_hash) and meta.total_size > 0,
           f'{meta.name} / {human_size(meta.total_size)} / {len(meta.files)} file(s)')

    engine = Engine(folder)
    try:
        import base64
        data = base64.b64encode(open(torrent_path, 'rb').read()).decode()
        gid = engine.client.call('aria2.addTorrent', data, [], {'dir': folder})
        # Connecting to peers is ours to get right; whether they then unchoke a
        # brand-new peer with nothing to trade is the swarm's decision.
        done, connections = 0, 0
        deadline = time.time() + 150
        while time.time() < deadline:
            status = engine.client.call('aria2.tellStatus', gid, [
                'status', 'completedLength', 'connections', 'numSeeders', 'downloadSpeed'])
            done = int(status.get('completedLength') or 0)
            connections = max(connections, int(status.get('connections') or 0))
            print(f'      torrent {human_size(done)} via {connections} peer(s)      ',
                  end='\r', flush=True)
            if done >= 1_000_000:
                break
            time.sleep(1)
        print()
        report('connects to BitTorrent peers', connections > 0, f'{connections} peer(s)')
        report('receives data from peers', done >= 1_000_000,
               f'{human_size(done)} in 150s'
               + ('' if done else ' - swarm never unchoked us; connections were fine'),
               external=True)
        engine.client.call('aria2.forceRemove', gid)
        time.sleep(1)

        # magnet metadata: same torrent, by hash only
        trackers = '&'.join(f'tr={t}' for t in meta.trackers[:4])
        magnet = f'magnet:?xt=urn:btih:{meta.info_hash}&dn={meta.name}' + (f'&{trackers}' if trackers else '')
        gid = engine.client.call('aria2.addUri', [magnet], {
            'dir': folder, 'bt-metadata-only': 'true', 'bt-save-metadata': 'true',
            'follow-torrent': 'false'})
        try:
            engine.wait(gid, timeout=120, label='magnet ')
        except TimeoutError:
            pass          # the file check below is the real verdict
        saved = os.path.join(folder, f'{meta.info_hash}.torrent')
        ok = os.path.exists(saved)
        # The engine-level magnet checks cover this properly; here it depends
        # on the same swarm that may not be talking to us.
        report('fetches metadata from a magnet link', ok,
               f'saved {human_size(os.path.getsize(saved))}' if ok
               else 'swarm did not send metadata in time', external=True)
        if ok:
            again = parse_torrent(open(saved, 'rb').read())
            report('magnet metadata matches the torrent', again.info_hash == meta.info_hash)
            report('file selection spec', select_file_spec([1], len(again.files)) in ('', '1'))
    finally:
        engine.stop()


def _make_settings(folder):
    settings = Settings()
    settings.download_dir = folder
    settings.embed_thumbnail = False   # keep the checks quick
    settings.embed_metadata = False
    settings.filename_template = '%(title).60B.%(ext)s'
    return settings


def check_probe(url, expect_kind=None, label=''):
    settings = _make_settings(tempfile.gettempdir())
    started = time.time()
    result = media_mod.probe(url, settings)
    elapsed = time.time() - started
    if result.error:
        report(f'{label}: probe', False, result.error)
        return None
    kinds = {}
    for item in result.items:
        kinds[item.kind] = kinds.get(item.kind, 0) + 1
    detail = (f'{result.kind}, {len(result.items)} item(s) {kinds}, site={result.site}, '
              f'{elapsed:.1f}s, title={result.title[:40]!r}')
    report(f'{label}: probe', bool(result.items) and (not expect_kind or result.kind == expect_kind), detail)
    if result.heights:
        report(f'{label}: quality options', True, f'heights={result.heights[:6]}')
    return result


class _JobRunner:
    """Drives a MediaJob without Qt, downloading through a real aria2."""

    def __init__(self, engine, task):
        self.engine = engine
        self.task = task
        self.events = []
        self.filepath = ''
        self.error = ''

    def callbacks(self):
        return {
            'emit': self.emit,
            'aria2_add': lambda tid, url, options: self.engine.client.call('aria2.addUri', [url], options),
            'aria2_status': lambda gid: self.engine.client.call('aria2.tellStatus', gid, [
                'status', 'completedLength', 'totalLength', 'downloadSpeed', 'errorMessage']),
            'aria2_control': self.control,
        }

    def control(self, gid, action):
        try:
            if action in ('forget', 'remove'):
                if action == 'remove':
                    self.engine.client.call('aria2.forceRemove', gid)
                self.engine.client.call('aria2.removeDownloadResult', gid)
        except Exception:
            pass

    def emit(self, task_id, event, payload):
        self.events.append(event)
        if event == 'progress':
            done, total = payload.get('done', 0), payload.get('total', 0)
            print(f'      {human_size(done)}/{human_size(total)} '
                  f'@ {human_size(payload.get("speed") or 0)}/s {payload.get("note", "")}   ',
                  end='\r', flush=True)
        elif event == 'state':
            print(f'      state: {payload.get("state")} {payload.get("note", "")}          ', flush=True)
        elif event == 'finished':
            self.filepath = payload.get('filepath', '')
        elif event == 'error':
            self.error = payload.get('message', '')


def check_media(url, folder, quality='best', label='media', expect_ext=None):
    from grabbit.tasks import Task
    settings = _make_settings(folder)
    engine = Engine(folder)
    try:
        result = media_mod.probe(url, settings)
        if result.error:
            report(f'{label}: probe', False, result.error)
            return
        item = result.items[0]
        task = Task(kind='media', source=item.url or url, save_dir=folder, name=item.title)
        task.media = {'quality': quality, 'container': settings.video_container,
                      'info': item.info, 'info_time': time.time()}
        runner = _JobRunner(engine, task)
        job = media_mod.MediaJob(task, settings, runner.callbacks())
        started = time.time()
        job.start()
        job.join(timeout=300)
        print()
        ok = bool(runner.filepath) and os.path.exists(runner.filepath)
        size = os.path.getsize(runner.filepath) if ok else 0
        report(f'{label}: download via aria2 + yt-dlp', ok,
               f'{os.path.basename(runner.filepath)} {human_size(size)} in {time.time()-started:.1f}s'
               if ok else runner.error)
        if ok and expect_ext:
            report(f'{label}: output format', runner.filepath.lower().endswith(expect_ext),
                   os.path.splitext(runner.filepath)[1])
        if ok:
            report(f'{label}: used aria2 for the stream', 'progress' in runner.events)
    finally:
        engine.stop()


def check_gallery(url, folder, label='gallery'):
    settings = _make_settings(folder)
    result = check_probe(url, 'gallery', label)
    if not result:
        return
    images = [i for i in result.items if i.kind == 'image']
    report(f'{label}: finds photo slides', bool(images), f'{len(images)} photo(s)')
    if not images:
        return
    report(f'{label}: slides have preview images', all(i.preview for i in images))
    engine = Engine(folder)
    try:
        gids = []
        for index, item in enumerate(images[:3], 1):
            headers = [f'{k}: {v}' for k, v in (item.headers or {}).items()]
            gids.append(engine.client.call('aria2.addUri', [item.direct_url], {
                'dir': folder, 'out': f'{label}_{index}.{item.ext or "jpg"}', 'header': headers}))
        sizes = []
        for gid in gids:
            status = engine.wait(gid, timeout=120, label=f'{label} ')
            sizes.append(int(status.get('completedLength') or 0))
        report(f'{label}: downloads the photos', all(s > 10_000 for s in sizes),
               ', '.join(human_size(s) for s in sizes))
    finally:
        engine.stop()


def check_magnet_engine(folder):
    """The magnet flow through the real Engine: metadata only, no content here.

    Regression test: with aria2 allowed to reuse saved metadata, a second add of
    a known magnet started downloading the whole torrent into Grabbit's own data
    folder instead of just fetching the file list.
    """
    print('\n== magnet through the engine ==')
    data_dir = os.path.join(folder, 'appdata')
    os.makedirs(data_dir, exist_ok=True)
    # Isolate settings/tasks/torrents. In a dev run this also moves where the
    # bundled ffmpeg/deno are looked up, so it has to be put back afterwards.
    previous_local_appdata = os.environ.get('LOCALAPPDATA')
    os.environ['LOCALAPPDATA'] = data_dir
    try:
        _check_magnet_engine(folder)
    finally:
        if previous_local_appdata is not None:
            os.environ['LOCALAPPDATA'] = previous_local_appdata


def _check_magnet_engine(folder):
    from PySide6.QtCore import QCoreApplication
    from grabbit.engine import Engine
    from grabbit.paths import torrents_dir
    from grabbit.settings import Settings
    from grabbit.tasks import State

    app = QCoreApplication.instance() or QCoreApplication([])
    settings = Settings()
    settings.download_dir = folder
    engine = Engine(settings)
    if not engine.start():
        report('magnet: engine starts', False)
        return
    report('magnet: engine starts', True, f'aria2 {engine.aria2_version()}')

    meta = parse_torrent(open(os.path.join(folder, 'test.torrent'), 'rb').read()) \
        if os.path.exists(os.path.join(folder, 'test.torrent')) else None
    if meta is None:
        url = latest_ubuntu_torrent()
        path = os.path.join(folder, 'test.torrent')
        urllib.request.urlretrieve(url, path)
        meta = parse_torrent(open(path, 'rb').read())

    magnet = f'magnet:?xt=urn:btih:{meta.info_hash}&dn={meta.name}'
    trackers = ''.join(f'&tr={t}' for t in meta.trackers[:3])
    ready = []
    engine.magnet_ready.connect(lambda task_id: ready.append(task_id))

    for attempt in ('from the swarm', 'from the saved copy'):
        ready.clear()
        task = engine.add_magnet(magnet + trackers, folder)
        if task is None:
            report(f'magnet: add ({attempt})', False, 'refused')
            break
        deadline = time.time() + 150
        while not ready and time.time() < deadline:
            app.processEvents()
            time.sleep(0.05)
        got = bool(ready)
        # Whether the swarm answers is out of our hands; what happens next is
        # not, and it is the part worth guarding. So a timeout here is a skip,
        # and the saved-copy attempt below still runs - its metadata is put in
        # place from the .torrent this test already fetched over HTTP.
        report(f'magnet: file list arrives {attempt}', got,
               f'{meta.name} ({len(meta.files)} file(s))' if got else 'timed out',
               external=not got)
        if not got:
            engine.remove([task.id], delete_files=False)
            app.processEvents()
            shutil.copyfile(path, torrents_dir() / f'{meta.info_hash.lower()}.torrent')
            continue
        stray = [f for f in os.listdir(torrents_dir()) if not f.endswith(('.torrent', '.aria2'))]
        report(f'magnet: downloads nothing until you choose ({attempt})', not stray,
               f'stray files: {stray}' if stray else 'data folder holds only metadata')
        current = engine.store.get(task.id)
        report(f'magnet: waits paused ({attempt})', current.state == State.PAUSED, current.state)
        engine.remove([task.id], delete_files=False)
        app.processEvents()

    engine.shutdown()


def check_gif(folder):
    """Downloading a video straight to an animated GIF."""
    print('\n== animated GIF ==')
    from grabbit.tasks import Task
    settings = _make_settings(folder)
    settings.gif_max_seconds = 5           # keep the check quick
    settings.gif_width = 320
    engine = Engine(folder)
    try:
        result = media_mod.probe(YOUTUBE_URL, settings)
        item = result.items[0]
        task = Task(kind='media', source=item.url or YOUTUBE_URL, save_dir=folder, name=item.title)
        task.media = {'quality': 'gif', 'container': 'mp4', 'info': item.info,
                      'info_time': time.time()}
        runner = _JobRunner(engine, task)
        job = media_mod.MediaJob(task, settings, runner.callbacks())
        job.start()
        job.join(timeout=300)
        print()
        path = runner.filepath
        ok = bool(path) and os.path.exists(path) and path.lower().endswith('.gif')
        report('gif: produces a .gif', ok,
               f'{os.path.basename(path)} {human_size(os.path.getsize(path))}' if ok else runner.error)
        if ok:
            with open(path, 'rb') as handle:
                header = handle.read(6)
            report('gif: the file is a real GIF', header in (b'GIF89a', b'GIF87a'), header.decode('latin1'))
            leftovers = [f for f in os.listdir(folder) if f.endswith(('.palette.png', '.mp4.palette.png'))]
            report('gif: cleans up after itself', not leftovers, 'no palette files left')
    finally:
        engine.stop()


def check_more_sources(folder):
    """Photo sites via gallery-dl, and reading media off an ordinary page."""
    print('\n== other sources ==')
    from grabbit import gallerydl, pagescrape
    settings = _make_settings(folder)

    exe = gallerydl.executable()
    report('gallery-dl runs as a separate program', bool(exe),
           os.path.basename(exe) if exe else 'gallery-dl.exe not found in tools')
    if exe:
        photo_url = 'https://commons.wikimedia.org/wiki/File:Felis_catus-cat_on_snow.jpg'
        result = gallerydl.probe(photo_url, settings)
        ok = bool(result and result.items)
        report('gallery-dl: finds photos on a photo site', ok,
               f'{len(result.items)} file(s), first is {result.items[0].ext}' if ok
               else 'nothing returned')

    scraped = pagescrape.scrape('https://en.wikipedia.org/wiki/Cat', settings)
    report('page scraper: finds media on an ordinary page', len(scraped.items) >= 5,
           f'{len(scraped.items)} files ({scraped.description})' if scraped.items else scraped.error)

    if scraped.items:
        engine = Engine(folder)
        try:
            item = scraped.items[0]
            headers = [f'{k}: {v}' for k, v in (item.headers or {}).items()]
            gid = engine.client.call('aria2.addUri', [item.direct_url], {
                'dir': folder, 'out': f'scraped.{item.ext or "bin"}', 'header': headers})
            status = engine.wait(gid, timeout=90, label='scraped ')
            size = int(status.get('completedLength') or 0)
            report('page scraper: the files actually download', size > 1000, human_size(size))
        finally:
            engine.stop()


def check_metalink(folder):
    """Metalink parsing, mirrors, and aria2 verifying the checksum."""
    print('\n== metalink ==')
    import hashlib
    from grabbit import metalink

    with urllib.request.urlopen(HTTP_URL, timeout=60) as response:
        payload = response.read()
    digest = hashlib.sha256(payload).hexdigest()
    document = f'''<?xml version="1.0" encoding="UTF-8"?>
<metalink xmlns="urn:ietf:params:xml:ns:metalink">
  <file name="metalink-check.zip">
    <size>{len(payload)}</size>
    <hash type="sha-256">{digest}</hash>
    <url priority="1">{HTTP_URL}</url>
    <url priority="2">{HTTP_URL}</url>
  </file>
</metalink>'''.encode()

    files = metalink.parse(document)
    ok = len(files) == 1 and len(files[0].urls) == 2
    report('metalink: reads files, mirrors and checksum', ok,
           f'{files[0].name}, {len(files[0].urls)} mirrors, {files[0].checksum[:24]}…' if ok else 'parse failed')
    if not ok:
        return

    engine = Engine(folder)
    try:
        entry = files[0]
        gid = engine.client.call('aria2.addUri', entry.urls, {
            'dir': folder, 'out': entry.name, 'checksum': entry.checksum})
        status = engine.wait(gid, timeout=120, label='metalink ')
        report('metalink: downloads and passes verification', status.get('status') == 'complete',
               human_size(int(status.get('completedLength') or 0)))

        # A wrong checksum must be rejected rather than silently accepted.
        gid = engine.client.call('aria2.addUri', entry.urls, {
            'dir': folder, 'out': 'metalink-bad.zip', 'checksum': 'sha-256=' + '0' * 64})
        rejected = False
        try:
            engine.wait(gid, timeout=120, label='bad-checksum ')
        except RuntimeError:
            rejected = True          # aria2 raised the checksum error
        report('metalink: a wrong checksum is rejected', rejected,
               'aria2 refused the file' if rejected else 'it was accepted anyway')
    finally:
        engine.stop()


def check_preview(url, label='preview'):
    """Resolving a playable stream and serving it through the local proxy."""
    from grabbit import streamserver
    settings = _make_settings(tempfile.gettempdir())
    try:
        stream = media_mod.resolve_stream(url, settings)
    except Exception as exc:
        report(f'{label}: finds a playable stream', False, str(exc)[:120])
        return
    report(f'{label}: finds a playable stream', bool(stream.get('url')),
           f'{stream.get("height")}p, audio={stream.get("has_audio")}, {stream.get("filename")[:40]}')

    server = streamserver.server()
    if stream.get('audio_url'):
        local = server.publish_muxed(stream['url'], stream['audio_url'],
                                     stream['headers'], stream['filename'])
    else:
        local = server.publish(stream['url'], stream['headers'], stream['filename'])
    try:
        request = urllib.request.Request(local, headers={'Range': 'bytes=0-65535'})
        with urllib.request.urlopen(request, timeout=45) as response:
            data = response.read(200_000)
            ranged = response.status == 206
        report(f'{label}: player can stream it', len(data) > 10_000,
               f'{response.status}, {len(data)} bytes, '
               + ('joined by ffmpeg' if stream.get('audio_url') else f'seekable={ranged}'))
    except Exception as exc:
        report(f'{label}: player can stream it', False, str(exc)[:120])
    finally:
        streamserver.server().stop()


def main():
    parser = argparse.ArgumentParser()
    for name in ('all', 'http', 'torrent', 'magnet', 'youtube', 'instagram', 'tiktok',
                 'preview', 'sources', 'metalink', 'gif', 'probe'):
        parser.add_argument(f'--{name}', action='store_true')
    parser.add_argument('--keep', action='store_true', help='keep the temporary download folder')
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.print_help()
        return 1

    folder = tempfile.mkdtemp(prefix='grabbit-selftest-')
    print(f'temporary folder: {folder}')

    def run(name, fn, *fn_args):
        """One failing check must not take the rest of the suite with it."""
        try:
            fn(*fn_args)
        except Exception as exc:
            report(f'{name}: crashed', False, f'{type(exc).__name__}: {exc}')

    try:
        if args.all or args.http:
            run('http', check_http, folder)
        if args.all or args.torrent:
            run('torrent', check_torrent, folder)
        if args.all or args.magnet:
            run('magnet', check_magnet_engine, folder)
        if args.all or args.youtube:
            print('\n== YouTube ==')
            run('youtube probe', check_probe, YOUTUBE_URL, 'video', 'youtube')
            run('youtube', check_media, YOUTUBE_URL, folder, 'best', 'youtube', '.mp4')
        if args.all or args.instagram:
            print('\n== Instagram carousel ==')
            run('instagram', check_gallery, INSTAGRAM_URL, folder, 'instagram')
        if args.all or args.tiktok:
            print('\n== TikTok ==')
            run('tiktok photos', check_gallery, TIKTOK_PHOTO_URL, folder, 'tiktok-photo')
            run('tiktok video probe', check_probe, TIKTOK_VIDEO_URL, 'video', 'tiktok-video')
            run('tiktok video', check_media, TIKTOK_VIDEO_URL, folder, 'best', 'tiktok-video', '.mp4')
        if args.all or args.preview:
            print('\n== watch before downloading ==')
            run('preview youtube', check_preview, YOUTUBE_URL, 'preview-youtube')
            run('preview tiktok', check_preview, TIKTOK_VIDEO_URL, 'preview-tiktok')
        if args.all or args.sources:
            run('other sources', check_more_sources, folder)
        if args.all or args.metalink:
            run('metalink', check_metalink, folder)
        if args.all or args.gif:
            run('gif', check_gif, folder)
        if args.probe:
            for url in (YOUTUBE_URL, INSTAGRAM_URL, TIKTOK_PHOTO_URL, TIKTOK_VIDEO_URL):
                check_probe(url, label=url.split('/')[2])
    finally:
        if not args.keep:
            shutil.rmtree(folder, ignore_errors=True)

    failures = [name for name, ok in results if ok is False]
    skipped = [name for name, ok in results if ok is None]
    passed = len(results) - len(failures) - len(skipped)
    print(f'\n{passed}/{len(results) - len(skipped)} checks passed'
          + (f', {len(skipped)} skipped (out of our hands)' if skipped else ''))
    for name in skipped:
        print(f'  skipped: {name}')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
