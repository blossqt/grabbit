"""Checks the phone's downloader and the window's side of it, on a desktop.

On a phone the downloads run in a process of their own (host.py) and the
window talks to it over a local socket (remote.py). Both ends are plain
Python, so here they run in one process, on two threads, with the desktop's
aria2 and files served from this machine: no phone, no network, a minute.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\android\\engine_check.py

The rules that decide when a failed video is tried again, what Wi-Fi only
holds back, how seeding picks up after a restart and when the downloader ends
itself are checked here too, against a stand-in for Android where they need
one.
"""

import os
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, os.path.join(ROOT, 'android'))

WORKSPACE = tempfile.mkdtemp(prefix='grabbit-engine-check-')
os.environ['ANDROID_PRIVATE'] = WORKSPACE

from grabbit import analyze as analyze_mod          # noqa: E402
from grabbit import paths as shared_paths           # noqa: E402
from grabbit.mediaitems import MediaItem, ProbeResult  # noqa: E402
from grabbit.settings import Settings               # noqa: E402
from grabbit.tasks import KIND_MEDIA, KIND_TORRENT, State, Task  # noqa: E402
from grabbit_mobile import paths as mobile_paths    # noqa: E402
from grabbit_mobile import wire                     # noqa: E402
from grabbit_mobile.background import Background, describe  # noqa: E402
from grabbit_mobile.engine import (FIRST_WAIT, RETRY_NETWORK, RETRY_REFUSED,  # noqa: E402
                                   WIFI_NOTE, MobileEngine)
from grabbit_mobile.host import IDLE, Host          # noqa: E402
from grabbit_mobile.remote import RemoteEngine      # noqa: E402

DOWNLOADS = Path(WORKSPACE) / 'Downloads'
mobile_paths.downloads_dir = lambda: DOWNLOADS.mkdir(parents=True, exist_ok=True) or DOWNLOADS

results = []


def report(name, ok, detail=''):
    results.append((name, bool(ok)))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''), flush=True)


def wait_until(condition, seconds=20.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return bool(condition())


# ------------------------------------------------------------- a web site
class Site(SimpleHTTPRequestHandler):
    """Files with byte ranges; anything under /slow/ trickles out."""

    def log_message(self, *args):
        pass

    def do_GET(self):
        slow = self.path.startswith('/slow/')
        path = Path(self.translate_path(self.path.replace('/slow/', '/', 1)))
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get('Range', '')
        if header.startswith('bytes='):
            first, _, last = header[6:].split(',')[0].partition('-')
            start = int(first) if first else max(0, size - int(last))
            end = min(size - 1, int(last)) if first and last else size - 1
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        with path.open('rb') as handle:
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = handle.read(min(16384, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
                    if slow:
                        time.sleep(0.05)            # about 300 KB/s a connection
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


def serve():
    folder = Path(tempfile.mkdtemp(prefix='grabbit-site-'))
    (folder / 'small.bin').write_bytes(os.urandom(300_000))
    (folder / 'large.bin').write_bytes(os.urandom(24_000_000))
    server = ThreadingHTTPServer(('127.0.0.1', 0),
                                 lambda *a: Site(*a, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f'http://127.0.0.1:{server.server_port}'


def file_link(url):
    return analyze_mod.Analysis(url=url, kind=analyze_mod.KIND_FILE, title=url.rsplit('/', 1)[-1],
                                filename=url.rsplit('/', 1)[-1])


# ------------------------------------------------------------------ checks
def check_wire():
    probe = ProbeResult(url='https://example.com/v', title='A video', items=[
        MediaItem(key='v1', title='A video', info={'id': 'v1', 'formats': [{'url': 'x'}]},
                  headers={'Referer': 'https://example.com'}, heights=[720, 360])])
    link = analyze_mod.Analysis(url='https://example.com/v', kind=analyze_mod.KIND_MEDIA,
                                title='A video', probe=probe, torrent_data=b'\x00\xffd8:announce')
    back = wire.decode(wire.encode(link))
    report('a read link crosses to the downloader whole - media items, bytes and all',
           back == link and isinstance(back.probe.items[0], MediaItem), type(back.probe).__name__)
    try:
        wire.decode({'__kind__': 'Popen', 'fields': {}})
        refused = False
    except ValueError:
        refused = True
    report('and nothing but a read link can be brought to life at the other end', refused)

    task = Task(kind=KIND_MEDIA, name='Clip', media={'quality': '720', 'info': {'huge': 'x' * 5000}},
                log=['one', 'two'], headers=['A: b'])
    state = wire.task_state(task)
    report("a snapshot carries a download without yt-dlp's reading of it or its log",
           'log' not in state and state['media'] == {'quality': '720'} and state['name'] == 'Clip',
           f'{len(str(state))} characters')


def check_rules():
    engine = MobileEngine(Settings())
    task = Task(kind=KIND_MEDIA, name='Clip')
    waits = [engine._retry_wait(task, 'Connection reset by peer') for _ in range(RETRY_NETWORK + 1)]
    report('a video that loses its connection is tried again, waiting longer each time',
           waits[0] == FIRST_WAIT and waits[1] == 2 * FIRST_WAIT and waits[-2] == 300
           and waits[-1] is None, ', '.join(str(int(w)) if w else '-' for w in waits[:6]) + '...')
    task.done = 50_000_000
    report('and one that got further before failing again starts the count afresh',
           engine._retry_wait(task, 'Read timed out') == FIRST_WAIT)

    refused = Task(kind=KIND_MEDIA, name='Clip')
    waits = [engine._retry_wait(refused, 'HTTP Error 403: Forbidden') for _ in range(RETRY_REFUSED + 1)]
    report('a refused video is read again for a fresh address - twice, then left failed',
           waits[:RETRY_REFUSED] == [5.0] * RETRY_REFUSED and waits[-1] is None, str(waits))
    report('a failure waiting cannot fix fails at once',
           engine._retry_wait(Task(kind=KIND_MEDIA), 'Unsupported URL: https://example.com') is None)

    offline = MobileEngine(Settings())
    offline.online = lambda: False
    queued = offline.store.add(Task(kind=KIND_MEDIA, name='Clip', state=State.QUEUED))
    offline._retry_at[queued.id] = time.monotonic() - 1
    offline._retry_due()
    held_back = queued.id in offline._retry_at and queued.progress_note == 'Waiting for a connection'
    offline.online = lambda: True
    offline._queue_media = lambda task: setattr(task, 'progress_note', 'queued')
    offline._retry_due()
    report('with no network at all it waits for one, and goes the moment there is',
           held_back and queued.id not in offline._retry_at and queued.progress_note == 'queued')

    settings = Settings()
    settings.seed_ratio = 1.0
    seeding = MobileEngine(settings)
    done = Task(kind=KIND_TORRENT, state=State.SEEDING, total=1000, uploaded=1200)
    report('a torrent that had given back enough before a restart is finished, not seeded again',
           seeding._seeded_enough(done) and done.state == State.COMPLETED)

    calls = []

    class Client:
        def call(self, method, *args):
            calls.append((method, args))
            return 'gid'

    seeding.client = Client()
    torrent = Path(WORKSPACE) / 'sample.torrent'
    torrent.write_bytes(b'd4:infod4:name1:ae')
    halfway = Task(kind=KIND_TORRENT, state=State.SEEDING, total=1000, uploaded=400,
                   torrent_file=str(torrent))
    seeding._add_torrent(halfway, seed_only=True)
    options = calls[-1][1][2] if calls else {}
    report('one halfway there seeds on, from its files as they are, for what is left',
           options.get('bt-seed-unverified') == 'true' and options.get('seed-ratio') == '0.600'
           and halfway.uploaded_base == 400, str({k: v for k, v in options.items() if 'seed' in k}))

    title, text, percent, upload = describe([Task(kind=KIND_TORRENT, name='ubuntu.iso',
                                                  state=State.SEEDING)], {'upload_speed': 500_000})
    report('seeding keeps the notification, with no bar and the upload speed',
           upload and percent == -2 and text.startswith('Seeding · ↑'), f'{title}: {text}')
    title, text, percent, upload = describe(
        [Task(kind=KIND_MEDIA, name='Clip', state=State.DOWNLOADING, total=100, done=40),
         Task(kind=KIND_TORRENT, name='ubuntu.iso', state=State.SEEDING)],
        {'download_speed': 1_000_000})
    report('a download beside it takes the notification, and the seeding is counted',
           not upload and percent == 40 and text.endswith('1 seeding'), f'{title}: {text}')


class Android:
    """DownloadService's static methods, as the downloader uses them."""
    foreground = False
    free = True
    refuse = False
    held_until = 0.0
    finished = False
    shown = []

    @classmethod
    def show(cls, title, text, percent, seeding):
        if not cls.foreground and cls.refuse:
            return False
        cls.foreground = True
        cls.shown.append((title, text, percent, seeding))
        return True

    @classmethod
    def hide(cls):
        if time.monotonic() < cls.held_until:
            return False
        cls.foreground = False
        return True

    @classmethod
    def isForeground(cls):
        return cls.foreground

    @classmethod
    def finish(cls):
        cls.finished = True

    @staticmethod
    def online():
        return True

    @classmethod
    def unmetered(cls):
        return cls.free


def check_background():
    Android.foreground, Android.refuse, Android.shown = False, False, []
    now = [100.0]
    notice = Background(Android, clock=lambda: now[0])
    notice.follow([Task(state=State.COMPLETED)], {})
    report('with nothing moving, the downloader stays out of the foreground', not Android.foreground)
    notice.follow([Task(kind=KIND_TORRENT, name='ubuntu.iso', state=State.SEEDING)], {})
    report('seeding brings it into the foreground - and keeps it there', Android.foreground)
    Android.held_until = time.monotonic() + 60
    notice.follow([], {})
    kept = Android.foreground
    Android.held_until = 0
    notice.follow([], {})
    report('a tap holds it there until the download reaches the engine, then it goes',
           kept and not Android.foreground)
    Android.refuse, attempts = True, len(Android.shown)
    for _ in range(5):
        notice.follow([Task(state=State.DOWNLOADING, total=10, done=1)], {})
        now[0] += 1
    report('refused, it asks again later rather than every second',
           not Android.foreground and len(Android.shown) == attempts)
    Android.refuse = False


def check_idle():
    Android.finished = False
    now = [0.0]
    host = Host(Settings(), service=None, clock=lambda: now[0])
    host.service = Android
    host.engine.ensure_running = lambda: True
    now[0] = IDLE - 1
    host.tick()
    early = host.stopping
    now[0] = IDLE + 5
    host.tick()
    report('with nothing to do and no window asking, the downloader ends itself - after a minute',
           not early and host.stopping and Android.finished)

    busy = Host(Settings(), service=None, clock=lambda: now[0])
    busy.engine.ensure_running = lambda: True
    busy.engine.store.add(Task(kind=KIND_TORRENT, state=State.SEEDING))
    now[0] += 10 * IDLE
    busy.tick()
    report('but not while anything is downloading or seeding', not busy.stopping)


def check_live(url):
    """The window's side and the downloader's, talking, with a real aria2."""
    settings = Settings()
    settings.save()
    hosts = []

    def launch():
        host = Host(Settings.load(), service=Android)
        hosts.append(host)
        host.start()
        threading.Thread(target=host.run, daemon=True).start()

    class Controls:
        """DownloadService as the window starts it: here, a thread."""
        launches = 0

        @classmethod
        def begin(cls, context):
            cls.launches += 1
            launch()

        @staticmethod
        def expect(context, title):
            Android.foreground = True

    messages = []
    remote = RemoteEngine(settings, on_message=lambda level, text: messages.append(text),
                          controls=Controls, context=None)
    remote.start()
    report('the window starts the downloader and connects to it',
           wait_until(lambda: remote.running, 20), f'aria2 {remote.version}')

    remote.add_analysis(file_link(f'{url}/small.bin'))
    report('a download asked for from the window appears in its list',
           wait_until(lambda: len(list(remote.store)) == 1, 10))
    task = next(iter(remote.store), None)
    report('and finishes, file and all',
           wait_until(lambda: task.state == State.COMPLETED, 20)
           and (DOWNLOADS / 'small.bin').stat().st_size == 300_000, task.status_text if task else '')
    report("the downloader's messages reach the window",
           wait_until(lambda: any('Added' in m for m in messages), 5), messages[-1] if messages else '')

    answers = []
    remote.fetch_log(task, answers.append)
    remote.fetch_files(task, answers.append)
    report('the details sheet can ask for a log and a file list',
           wait_until(lambda: len(answers) == 2, 5) and isinstance(answers[0], list),
           f'{len(answers)} answer(s)')

    remote.add_analysis(file_link(f'{url}/slow/large.bin'))
    wait_until(lambda: len(list(remote.store)) == 2, 10)
    large = next(t for t in remote.store if t.name == 'large.bin')
    wait_until(lambda: large.state == State.DOWNLOADING and large.done > 0, 20)
    remote.pause([large.id])
    report('pausing from the window pauses it there',
           wait_until(lambda: hosts[-1].engine.store.get(large.id).state == State.PAUSED, 10))
    time.sleep(1.5)                     # what was on its way when it paused is counted
    held = large.done
    time.sleep(2.0)
    report('and it stays paused', large.state == State.PAUSED and large.done == held,
           f'{large.done - held} bytes since')

    settings.wifi_only = True
    settings.save()
    Android.free = False
    remote.settings_changed()
    remote.resume([large.id])
    report('Wi-Fi only, off Wi-Fi: a download started by hand waits',
           wait_until(lambda: large.state == State.QUEUED and large.progress_note == WIFI_NOTE, 10)
           and remote.held, large.status_text)
    Android.free = True
    report('and goes by itself once the phone is back on Wi-Fi',
           wait_until(lambda: large.state == State.DOWNLOADING, 10), large.status_text)
    Android.free = False
    report('and waits again when it leaves Wi-Fi - a wait, not a pause',
           wait_until(lambda: large.state == State.QUEUED, 10) and large.progress_note == WIFI_NOTE,
           large.status_text)
    settings.wifi_only = False
    settings.save()
    remote.settings_changed()
    report('turning Wi-Fi only off lets it go at once',
           wait_until(lambda: large.state == State.DOWNLOADING, 10), large.status_text)

    settings.android_downloads_at_once = 2
    settings.save()
    remote.settings_changed()
    engine = hosts[-1].engine
    report('a setting changed in the window reaches aria2 without a restart', wait_until(
        lambda: engine.client.call('aria2.getGlobalOption').get('max-concurrent-downloads') == '2', 5))

    # The downloader goes - on a phone, Android ending its process - and the
    # window finds it gone, starts it again, and carries on where it was.
    before = Controls.launches
    first = hosts[-1]
    first.stop()
    report('the window notices when the downloader has gone', wait_until(lambda: not remote.connected, 10))
    report('starts it again, and is back in touch with it',
           wait_until(lambda: Controls.launches > before and remote.running, 30),
           f'{Controls.launches - before} restart(s)')
    large = remote.store.get(large.id)
    report('and the download picks up where it was',
           large is not None and wait_until(lambda: large.state in (State.DOWNLOADING,
                                                                     State.COMPLETED), 20),
           large.status_text if large else 'gone')

    remote.remove([large.id], delete_files=True)
    report('removing it from the window, file and all, is done there',
           wait_until(lambda: hosts[-1].engine.store.get(large.id) is None, 10)
           and wait_until(lambda: not (DOWNLOADS / 'large.bin').exists(), 10))

    # The window closes; the downloads do not.
    remote.shutdown()
    report("closing the window leaves the downloader running", not hosts[-1].stopping)
    for host in hosts:
        host.stop()


def main():
    # The notification's arrows, on a console that may be cp1252.
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    print(f'workspace: {WORKSPACE}')
    aria2 = shared_paths.find_tool('aria2c')
    report('aria2 is here to stand in for the phone one', bool(aria2), aria2 or 'build it first')
    mobile_paths.TOOL_PATHS['aria2c'] = aria2
    check_wire()
    check_rules()
    check_background()
    check_idle()
    server, url = serve()
    try:
        if aria2:
            check_live(url)
    finally:
        server.shutdown()

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
