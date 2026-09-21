"""Run the phone interface on a desktop, with downloads that are not real.

Building an APK to look at a layout takes minutes; this takes seconds. Kivy
runs anywhere, and the interface only needs a task store to draw, so a handful
of made-up tasks is enough to see every state at once.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\android\\preview_ui.py
    ... --shot out.png     render once, save it, and quit
    ... --update           with the banner a newer release would show
"""

import math
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, os.path.join(ROOT, 'android'))

os.environ.setdefault('ANDROID_PRIVATE', tempfile.mkdtemp(prefix='grabbit-preview-'))
os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.config import Config                              # noqa: E402

# A phone-shaped window, so the layout is judged at the size it will be used.
Config.set('graphics', 'width', '412')
Config.set('graphics', 'height', '892')
Config.set('graphics', 'resizable', '1')

from kivy.base import EventLoop                             # noqa: E402
from kivy.clock import Clock                                # noqa: E402
from kivy.core.window import Window                         # noqa: E402
from kivy.input.motionevent import MotionEvent              # noqa: E402

from grabbit.tasks import (KIND_HTTP, KIND_IMAGE, KIND_MEDIA, KIND_TORRENT,  # noqa: E402
                           State, Task, TaskStore)

import main as phone                                        # noqa: E402


class FakeEngine:
    """Enough of MobileEngine for the interface to draw itself."""

    process = None          # the real engine has an aria2 behind this

    def __init__(self, settings, on_change=None, on_message=None):
        self.settings = settings
        self.store = TaskStore()
        self.running = True
        self.stats = {'download_speed': 4_100_000, 'upload_speed': 620_000}
        self.on_change = on_change or (lambda: None)
        self.on_message = on_message or (lambda level, text: None)
        self._tick = 0
        for task in _sample_tasks():
            self.store.add(task)

    def start(self):
        return True

    def add_link(self, url, quality=''):
        self.on_message('info', f'Reading the link… ({url[:40]})')

    def pause(self, ids):
        for task_id in ids:
            task = self.store.get(task_id)
            if task:
                task.state = State.PAUSED
                task.down_speed = task.up_speed = 0

    def resume(self, ids):
        for task_id in ids:
            task = self.store.get(task_id)
            if task:
                task.state = State.DOWNLOADING

    def remove(self, ids, delete_files=False):
        for task_id in ids:
            self.store.remove(task_id)

    def fetch_files(self, task, callback):
        callback([{'path': f'/storage/emulated/0/Download/Grabbit/{task.name}',
                   'length': str(task.total), 'completedLength': str(task.done),
                   'selected': 'true'}])

    def fetch_peers(self, task, callback):
        callback([
            {'ip': '81.2.69.144', 'port': '51413', 'peerId': '-qB5000-abcdefghijkl',
             'downloadSpeed': '820000', 'uploadSpeed': '10240', 'bitfield': 'ffffff'},
            {'ip': '203.0.113.7', 'port': '6881', 'peerId': '-TR4060-xyzxyzxyzxyz',
             'downloadSpeed': '0', 'uploadSpeed': '240000', 'bitfield': '0fff00'},
        ])

    def fetch_status(self, task, keys, callback):
        callback({'bittorrent': {'announceList': [['https://torrent.ubuntu.com/announce'],
                                                  ['https://ipv6.torrent.ubuntu.com/announce']]}})

    def shutdown(self):
        self.running = False

    # The preview moves the numbers around so the graph has something to draw.
    def advance(self):
        self._tick += 1
        phase = self._tick / 6.0
        for task in self.store:
            if task.state in (State.DOWNLOADING, State.EXTRACTING):
                task.down_speed = max(0, int(2_600_000 + 1_800_000 * math.sin(phase + hash(task.id) % 7)))
                task.done = min(task.total, task.done + task.down_speed // 4)
                task.eta = (task.total - task.done) / max(1, task.down_speed)
            elif task.state == State.SEEDING:
                task.up_speed = max(0, int(700_000 + 400_000 * math.sin(phase * 0.8)))
        self.stats = {
            'download_speed': sum(t.down_speed for t in self.store),
            'upload_speed': sum(t.up_speed for t in self.store),
        }


class Tap(MotionEvent):
    """A touch, made the way the mouse provider makes one.

    is_touch and type_id are not decoration: without them the window's motion
    filter drops the event and nothing in the interface ever sees it.
    """

    def depack(self, args):
        self.is_touch = True
        self.sx, self.sy = args[0], args[1]
        self.profile = ['pos']
        super().depack(args)


def reveal(widget):
    """Scroll a widget into view, if it lives in something that scrolls.

    The filter strip is wider than the screen; a chip that is off to the right
    cannot be tapped where it thinks it is, because something else is drawn
    there.
    """
    from kivy.uix.scrollview import ScrollView
    parent = widget.parent
    while parent is not None and not isinstance(parent, ScrollView):
        parent = parent.parent
    if parent is None:
        return
    content = parent.children[0]
    span = max(1.0, content.width - parent.width)
    parent.scroll_x = min(1.0, max(0.0, (widget.center_x - parent.width / 2) / span))
    settle()


def settle(frames: int = 3):
    """Let Kivy finish laying out before anything is measured or touched."""
    for _ in range(frames):
        EventLoop.idle()


def tap(widget):
    """Touch the middle of a widget, through Kivy's own event loop.

    The position goes through to_window because a row inside the scrolling
    list is positioned in the list's coordinates, not the window's.
    """
    settle()
    x, y = widget.to_window(*widget.center)
    touch = Tap('preview', 1, [x / Window.width, y / Window.height],
                is_touch=True, type_id='touch')
    EventLoop.post_dispatch_input('begin', touch)
    EventLoop.post_dispatch_input('end', touch)
    settle(1)


def check(app):
    """Tap through everything and report it, the way the other tests do."""
    results = []

    def report(name, ok, detail=''):
        results.append((name, ok))
        print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''))

    # Updates first, so every check after this one also shows that a banner
    # which has come and gone leaves nothing behind to catch taps.
    from grabbit import APP_VERSION, updates
    app.refresh()
    report('the footer carries this version', f'Grabbit {APP_VERSION}' in app.footer.text,
           app.footer.text)
    apk = updates.Asset('Grabbit-9.0.0-arm64.apk', 1, '0' * 64, 'https://example.invalid/apk')
    newer = updates.Check(APP_VERSION, updates.Release('9.0.0', 'now', '', None, apk), apk)
    app._on_update_checked(newer, False)
    settle()
    report('a newer Grabbit shows the update banner',
           app.update_banner.parent is app.update_slot and '9.0.0' in app.update_label.text,
           app.update_label.text)
    tap(app._format_chips['audio_mp3'])
    report('the controls below it still take taps', app.format == 'audio_mp3', repr(app.format))
    tap(app.update_later)
    settle()
    report('Later takes it away entirely',
           not app.update_slot.children and app.update_slot.height == 0)
    tap(app._format_chips[''])

    tap(app._format_chips['gif'])
    report('the format chips choose a format', app.format == 'gif', repr(app.format))
    tap(app._format_chips[''])

    reveal(app._status_chips['completed'])
    tap(app._status_chips['completed'])
    completed = [t for t in app.engine.store if t.state == State.COMPLETED]
    report('a status filter narrows the list', app.status_filter == 'completed'
           and len(app._rows) == len(completed), f'{len(app._rows)} rows shown')
    reveal(app._status_chips['all'])
    tap(app._status_chips['all'])

    reveal(app._kind_chips['torrent'])
    tap(app._kind_chips['torrent'])
    report('a type filter narrows the list', app.kind_filter == 'torrent'
           and all(t.is_torrent for t in app.engine.store if t.id in app._rows),
           f'{len(app._rows)} rows shown')
    reveal(app._kind_chips['all'])
    tap(app._kind_chips['all'])

    was = app.graph_shown
    tap(app.graph_chip)
    report('the graph toggle works', app.graph_shown != was,
           f'{"shown" if app.graph_shown else "hidden"}')
    if not app.graph_shown:
        tap(app.graph_chip)

    first = next(iter(app.engine.store))
    app.select_task('')          # start from nothing selected
    row = app._rows[first.id]
    tap(row.title)
    chosen = app.selected_id == first.id and first.name in app.graph.caption.text
    tap(row.title)               # and tapping it again goes back to the totals
    report('tapping a name picks that download for the graph',
           chosen and not app.selected_id, app.graph.caption.text[:40])

    running = next((t for t in app.engine.store if t.state == State.DOWNLOADING), None)
    if running is not None:
        tap(app._rows[running.id].detail)
        report('tapping the status line pauses it',
               app.engine.store.get(running.id).state == State.PAUSED)
        tap(app._rows[running.id].detail)
        report('and tapping it again starts it',
               app.engine.store.get(running.id).state != State.PAUSED)

    app.open_details(first.id)
    opened = app.details.title.text == (first.name or first.source)
    report('the details sheet opens on the right download', opened,
           app.details.title.text[:40])
    for name in ('Files', 'Peers', 'Trackers', 'Log', 'General'):
        tap(app.details.tab_chips[name])
        if app.details.tab != name:
            report(f'the {name} tab', False)
            break
    else:
        report('every details tab opens', True, 'General, Files, Peers, Trackers, Log')
    app.details.dismiss()

    failures = [name for name, ok in results if not ok]
    print()
    print(f'{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


def _sample_tasks():
    now = time.time()
    folder = '/storage/emulated/0/Download/Grabbit'
    return [
        Task(kind=KIND_MEDIA, source='https://www.youtube.com/watch?v=jNQXAC9IVRw',
             name='Me at the zoo [jNQXAC9IVRw].mp4', save_dir=folder,
             state=State.DOWNLOADING, total=44_800_000, done=19_300_000,
             down_speed=3_400_000, connections=8, added_at=now - 90,
             media={'quality': 'best'}, log=['Reading the page', 'Downloading 2 parts']),
        Task(kind=KIND_TORRENT, source='magnet:?xt=urn:btih:9ecd4676fd0f',
             name='ubuntu-24.04.3-desktop-amd64.iso', save_dir=folder,
             state=State.SEEDING, total=5_170_000_000, done=5_170_000_000,
             up_speed=910_000, seeds=5, connections=23, uploaded=3_100_000_000,
             info_hash='9ecd4676fd0f0474151a4b74a5958f42639cebdf',
             added_at=now - 4200, completed_at=now - 600),
        Task(kind=KIND_MEDIA, source='https://www.tiktok.com/@jade.wood/video/7434103280294300960',
             name='did you know there are two types of tiktok photo mode.mp4',
             save_dir=folder, state=State.COMPLETED, total=2_742_793, done=2_742_793,
             completed_at=now - 300, added_at=now - 360, media={'quality': 'best'}),
        Task(kind=KIND_IMAGE, source='https://scontent.cdninstagram.com/v/NASA-01.jpg',
             name='NASA - 01.jpg', save_dir=folder, state=State.COMPLETED,
             total=294_861, done=294_861, added_at=now - 280, completed_at=now - 270),
        Task(kind=KIND_MEDIA, source='https://www.youtube.com/watch?v=aqz-KE-bpKQ',
             name='Big Buck Bunny.mp3', save_dir=folder, state=State.PAUSED,
             total=9_400_000, done=2_100_000, added_at=now - 500,
             media={'quality': 'audio_mp3'}),
        Task(kind=KIND_HTTP, source='https://example.com/handbook.pdf',
             name='handbook.pdf', save_dir=folder, state=State.ERROR,
             total=0, done=0, error='403 Forbidden', added_at=now - 120),
    ]


def main():
    phone.MobileEngine = FakeEngine
    app = phone.GrabbitApp()

    # No phone here, so skip the parts that talk to Android.
    app._start_engine = lambda: app._schedule_message('info', 'Preview - nothing is real')
    shot = None
    if '--shot' in sys.argv:
        shot = sys.argv[sys.argv.index('--shot') + 1]

    def tick(_):
        app.engine.advance()
        app.refresh()

    def ready(_):
        app.show_graph(True)
        for _ in range(40):
            app.engine.advance()
            app.graph.push(app.engine.stats['download_speed'], app.engine.stats['upload_speed'])
            app.graph.push_tasks(app.engine.store)
        app.select_task(list(app.engine.store)[0].id)
        app.refresh()
        if '--update' in sys.argv:
            from grabbit import APP_VERSION, updates
            apk = updates.Asset('Grabbit-1.3.0-arm64.apk', 1, '0' * 64, updates.RELEASES_PAGE)
            sample = updates.Check(APP_VERSION, updates.Release('1.3.0', 'now', '', None, apk), apk)
            app.show_update(sample)
        if '--check' in sys.argv:
            code = check(app)
            app.stop()
            sys.exit(code)
        Clock.schedule_interval(tick, 1.0)
        if shot:
            def sheet(_):
                Window.screenshot(name=shot)
                if '--details' in sys.argv:
                    app.open_details(list(app.engine.store)[1].id)
                    # Every tab, so a broken one shows up here rather than on
                    # a phone.
                    for name in ('Files', 'Peers', 'Trackers', 'Log', 'General'):
                        app.details.show_tab(name)
                    wanted = sys.argv[sys.argv.index('--tab') + 1] if '--tab' in sys.argv else 'General'
                    app.details.show_tab(wanted)
                    Clock.schedule_once(
                        lambda _: (Window.screenshot(name=shot), app.stop()), 0.8)
                else:
                    app.stop()
            Clock.schedule_once(sheet, 0.6)

    Clock.schedule_once(ready, 0.4)
    app.run()


if __name__ == '__main__':
    main()
