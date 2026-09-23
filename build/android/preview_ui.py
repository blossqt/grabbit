"""Run the phone interface on a desktop, with downloads that are not real.

Building an APK to look at a layout takes minutes; this takes seconds. Kivy
runs anywhere, and the interface only needs a task store to draw, so a handful
of made-up tasks is enough to see every state at once.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\android\\preview_ui.py
    ... --shot out.png     render once, save it, and quit
    ... --update           with the banner a newer release would show
    ... --page video       with the page a pasted link opens (video, audio,
                           gif or image), for a video served from this machine
    ... --dialog remove    with a dialog open (remove, or storage)
    ... --settings         with the settings page open
    ... --choose           with two downloads picked out, as holding one does
    ... --width 360        as narrow as a smaller phone (412 by default)
"""

import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, os.path.join(ROOT, 'android'))
sys.path.insert(0, os.path.join(ROOT, 'build'))

os.environ.setdefault('ANDROID_PRIVATE', tempfile.mkdtemp(prefix='grabbit-preview-'))
os.environ.setdefault('KIVY_NO_ARGS', '1')

from kivy.config import Config                              # noqa: E402

# A phone-shaped window, so the layout is judged at the size it will be used.
Config.set('graphics', 'width', sys.argv[sys.argv.index('--width') + 1] if '--width' in sys.argv else '412')
Config.set('graphics', 'height', '892')
Config.set('graphics', 'resizable', '1')

from kivy.base import EventLoop                             # noqa: E402
from kivy.clock import Clock                                # noqa: E402
from kivy.core.window import Window                         # noqa: E402
from kivy.input.motionevent import MotionEvent              # noqa: E402

from grabbit import analyze as analyze_mod                  # noqa: E402
from grabbit.mediaitems import MediaItem, ProbeResult       # noqa: E402
from grabbit.tasks import (KIND_HTTP, KIND_IMAGE, KIND_MEDIA, KIND_TORRENT,  # noqa: E402
                           State, Task, TaskStore)

import main as phone                                        # noqa: E402


class SampleSite:
    """A video served from this machine, as a site would serve it.

    The page's frame chooser reads real frames through yt-dlp and FFmpeg, so
    it needs a real video to read them from: frames_check.py's, whose
    brightness climbs steadily, with a thumbnail beside it.
    """

    def __init__(self):
        from http.server import ThreadingHTTPServer
        from pathlib import Path

        import frames_check
        from grabbit.paths import find_tool
        self.folder = tempfile.mkdtemp(prefix='grabbit-sample-')
        video = frames_check.make_video(Path(self.folder))
        subprocess.run([find_tool('ffmpeg'), '-v', 'error', '-y', '-ss', '5', '-i', str(video),
                        '-frames:v', '1', os.path.join(self.folder, 'thumb.jpg')], check=True)
        self.server = ThreadingHTTPServer(
            ('127.0.0.1', 0), lambda *a: frames_check.RangeHandler(*a, directory=self.folder))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f'http://127.0.0.1:{self.server.server_port}'
        self.url = f'{base}/climb.mp4'
        self.thumbnail = f'{base}/thumb.jpg'
        self.duration = float(frames_check.SECONDS)

    def analysis(self, url=None):
        item = MediaItem(key='climb', kind='video', title='A video that gets brighter',
                         thumbnail=self.thumbnail, preview=self.thumbnail,
                         duration=self.duration, url=self.url,
                         heights=[1080, 720, 480, 360, 240])
        probe = ProbeResult(url=url or self.url, kind='video', site='Sample',
                            title=item.title, uploader='Grabbit', items=[item],
                            heights=item.heights)
        return analyze_mod.Analysis(url=url or self.url, kind=analyze_mod.KIND_MEDIA,
                                    title=item.title, probe=probe)

    def close(self):
        self.server.shutdown()
        shutil.rmtree(self.folder, ignore_errors=True)


class FakeEngine:
    """Enough of RemoteEngine for the interface to draw itself."""

    site = None             # a SampleSite, when the page is being looked at
    controls = context = None       # DownloadService, on a phone

    def __init__(self, settings, on_change=None, on_message=None, controls=None,
                 context=None, schedule=None):
        self.settings = settings
        self.store = TaskStore()
        self.running = self.connected = True
        self.version = '1.37.0'
        self.held = False
        self.stats = {'download_speed': 4_100_000, 'upload_speed': 620_000}
        self.on_change = on_change or (lambda: None)
        self.on_message = on_message or (lambda level, text: None)
        self._tick = 0
        self.added = []
        self.expected = []
        self.settings_sent = 0
        for task in _sample_tasks():
            self.store.add(task)

    def start(self):
        return True

    def expect(self, title):
        self.expected.append(title)

    def set_active(self, active):
        pass

    def settings_changed(self):
        self.settings_sent += 1

    def analyze_link(self, url, on_done):
        """Magnets and the like are read for real - there is no network in
        that; any web link stands for the sample video."""
        def work():
            time.sleep(0.3)
            if url.startswith('magnet:') or self.site is None:
                on_done(analyze_mod.analyze(url, self.settings))
            else:
                on_done(self.site.analysis(url))
        threading.Thread(target=work, daemon=True).start()

    def add_analysis(self, result, choice=None):
        self.added.append((result, dict(choice or {})))
        self.store.add(Task(kind=KIND_MEDIA, source=result.url, name=result.title or result.url,
                            state=State.QUEUED, media=dict(choice or {})))
        self.on_change()

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

    def fetch_log(self, task, callback):
        callback(list(task.log))

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
    if content.width > parent.width:
        span = content.width - parent.width
        parent.scroll_x = min(1.0, max(0.0, (widget.center_x - parent.width / 2) / span))
    else:
        parent.scroll_to(widget, padding=10, animate=False)
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
    lift(touch)
    settle(1)


def lift(touch):
    """The finger leaving the glass - timed, as Kivy's own providers time it."""
    touch.update_time_end()
    EventLoop.post_dispatch_input('end', touch)


def wait_until(condition, seconds: float = 10.0) -> bool:
    """Run the interface until something is true, or the time is up."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        EventLoop.idle()
        if condition():
            return True
        time.sleep(0.02)
    return bool(condition())


def drag(widget, start: float, end: float, steps: int = 8):
    """A finger along a widget, from one fraction of its width to another."""
    settle()
    y = widget.to_window(*widget.center)[1]
    x0 = widget.to_window(widget.x + widget.width * start, 0)[0]
    x1 = widget.to_window(widget.x + widget.width * end, 0)[0]
    touch = Tap('preview', 4, [x0 / Window.width, y / Window.height],
                is_touch=True, type_id='touch')
    EventLoop.post_dispatch_input('begin', touch)
    for step in range(1, steps + 1):
        x = x0 + (x1 - x0) * step / steps
        touch.move([x / Window.width, y / Window.height])
        EventLoop.post_dispatch_input('update', touch)
        EventLoop.idle()
    lift(touch)
    settle(1)


def brightness_time(texture) -> float:
    """When in the sample video a frame is from: its brightness says so."""
    pixels = texture.pixels
    reds = pixels[0::4]
    full = sum(reds) / max(1, len(reds))
    return (full * 219 / 255) / 10          # frames_check.RATE a second, from 16


def check_page(app, report):
    """A pasted link: the page that asks what to make of it."""
    from grabbit import frames
    from kivy.metrics import dp
    site = SampleSite()
    app.engine.site = site
    try:
        app.input.text = site.url
        tap(app.download_button)
        page = app.page
        report('Download opens a page for the link straight away',
               page is not None and page.parent is Window, page.about.text if page else 'no page')
        wait_until(lambda: page.analysis is not None, 10)
        report('which fills in once the link has been read',
               page.title.text == 'A video that gets brighter' and 'Sample' in page.about.text,
               page.about.text)
        report('the page keeps clear of the camera, as the main screen does',
               page.column.padding[1] >= dp(10) + app._insets[0])
        report('a video can be saved as a video, its sound, a GIF or one picture',
               list(page.type_row.buttons) == ['video', 'audio', 'gif', 'image'],
               ', '.join(b.text for b in page.type_row.buttons.values()))
        wait_until(lambda: page.picture.opacity == 1, 10)
        report('with its thumbnail', page.picture.opacity == 1 and page.picture.texture is not None)
        report('Video offers the qualities that video comes in, and file types',
               list(page.quality_row.buttons) == ['best', '1080', '720', '480', '360', '240']
               and list(page.container_row.buttons) == ['mp4', 'mkv', 'any'],
               ', '.join(b.text for b in page.quality_row.buttons.values()))
        tap(page.quality_row.buttons['720'])
        tap(page.container_row.buttons['mkv'])
        report('choosing one chooses only that one', page.quality == '720'
               and page.quality_row.buttons['720'].selected
               and not page.quality_row.buttons['best'].selected)
        tap(page.type_row.buttons['audio'])
        report('Audio offers MP3 and M4A', list(page.audio_row.buttons) == ['audio_mp3', 'audio_m4a'])
        tap(page.audio_row.buttons['audio_m4a'])
        tap(page.go)
        wait_until(lambda: page.parent is None, 3)       # it fades out first
        result, choice = app.engine.added[-1] if app.engine.added else (None, {})
        report('Download queues exactly that, and the page closes',
               choice.get('quality') == 'audio_m4a' and page.parent is None and app.page is None,
               str(choice))

        app.input.text = site.url
        tap(app.download_button)
        page = app.page
        wait_until(lambda: page.analysis is not None, 10)
        report('the page remembers the last choice', page.kind == 'audio'
               and page.audio == 'audio_m4a', f'{page.kind}, {page.audio}')
        tap(page.type_row.buttons['image'])
        report('Image shows a slider along the whole video', page.scrubber.duration == site.duration,
               f'{page.scrubber.duration:.0f}s')
        wait_until(lambda: page._frame_texture is not None, 40)
        first = page._frame_texture
        report('and the frame at the start', first is not None and page.picture.texture is first,
               page.picture_note.text)
        drag(page.scrubber, 0.0, 0.5)
        middle = site.duration / 2
        report('dragging moves the moment chosen', abs(page.frame_at - middle) < site.duration * 0.08,
               frames.clock(page.frame_at))
        wait_until(lambda: page._frame_texture is not first, 30)
        shown = brightness_time(page._frame_texture) if page._frame_texture is not first else -1
        report('and the frame from there is shown once the finger stops',
               abs(shown - page.frame_at) < 1.0,
               f'wanted {page.frame_at:.1f}s, shows {shown:.1f}s')
        before = page.frame_at
        tap(page.step_forward)
        report('› steps on a little', 0 < page.frame_at - before <= 0.2,
               f'{before:.2f}s -> {page.frame_at:.2f}s')
        tap(page.frame_row.buttons['jpg'])
        tap(page.go)
        wait_until(lambda: page.parent is None, 3)
        result, choice = app.engine.added[-1]
        report('Download queues that one frame, as a JPG',
               choice.get('quality') == 'frame' and choice.get('frame_format') == 'jpg'
               and abs(choice.get('frame_at', -1) - page.frame_at) < 0.01, str(choice))

        count = len(app.engine.added)
        app.input.text = ('magnet:?xt=urn:btih:9ecd4676fd0f0474151a4b74a5958f42639cebdf'
                          '&dn=ubuntu-24.04.3-desktop-amd64.iso')
        tap(app.download_button)
        page = app.page
        wait_until(lambda: page.analysis is not None, 10)
        report('a magnet link gets the page too, with nothing to choose',
               'BitTorrent' in page.about.text and not page.choices.children, page.about.text)
        page.dismiss()
        wait_until(lambda: page.parent is None, 3)
        report('closing it adds nothing', len(app.engine.added) == count and app.page is None)
        app.input.text = ''
    finally:
        app.engine.site = None
        site.close()


def open_dialog():
    from grabbit_mobile.ui.widgets import Dialog
    return next((child for child in Window.children if isinstance(child, Dialog)), None)


def labels_clear(buttons, room=None) -> bool:
    """Every label fits its button whole, with room either side - by default
    the room a dialog asks for."""
    from kivy.core.text import Label as CoreLabel
    from grabbit_mobile.ui.widgets import Dialog
    room = Dialog.ROOM if room is None else room
    return all(CoreLabel(font_size=b.font_size).get_extents(b.text)[0] + 2 * room
               <= b.width + 0.5 for b in buttons)


def check_dialogs(app, report):
    """The app's dialogs: one rounded card for all of them, labels clear of edges."""
    from kivy.metrics import dp
    from grabbit_mobile.ui.widgets import Card

    task = next(iter(app.engine.store))
    count = len(list(app.engine.store))
    app.confirm_remove(task.id)
    settle(6)
    dialog = open_dialog()
    card = dialog.children[0] if dialog is not None else None
    report('removing a download asks in a rounded card, as the rest of the app is drawn',
           isinstance(card, Card) and card._radius >= dp(12),
           f'{card._radius / dp(1):.0f}dp corners' if isinstance(card, Card) else 'no dialog')
    if dialog is None:
        return
    buttons = list(reversed(dialog.buttons.children))
    report('its three choices stack, full width, the main one on top and Cancel last',
           dialog.buttons.orientation == 'vertical'
           and [b.text for b in buttons] == ['Delete it too', 'Keep the file', 'Cancel'],
           ' / '.join(b.text for b in buttons))
    report("and every label keeps clear of its button's edges", labels_clear(buttons))
    inside = all(dialog.x + card.padding[0] - 1 <= b.x and b.right <= dialog.right - card.padding[2] + 1
                 for b in buttons)
    report('inside the card, with its padding around them', inside)
    tap(buttons[-1])
    closed = wait_until(lambda: open_dialog() is None, 3)
    report('Cancel closes it and removes nothing',
           closed and len(list(app.engine.store)) == count)

    app.ask_for_storage()
    settle(6)
    dialog = open_dialog()
    buttons = list(reversed(dialog.buttons.children)) if dialog else []
    texts = [b.text for b in buttons]
    if dialog is not None and dialog.buttons.orientation == 'horizontal':
        report('two choices that fit share a row, the main one on the right',
               texts == ['Not now', 'Open settings'] and labels_clear(buttons), ' | '.join(texts))
    else:
        report('two choices too wide to share a row stack, the main one on top',
               texts == ['Open settings', 'Not now'] and labels_clear(buttons), ' / '.join(texts))
    if dialog is not None:
        dialog.dismiss()
        wait_until(lambda: open_dialog() is None, 3)


def check_settings(app, report):
    """The settings page: every setting saved as it is chosen, and the
    downloader told; Check for updates answering on the page itself."""
    from kivy.metrics import dp
    from grabbit import APP_VERSION, updates
    from grabbit_mobile.ui.widgets import BUTTON_MARGIN

    tap(app.settings_button)
    page = app.settings_page
    wait_until(lambda: page is not None and page.parent is Window, 3)
    report('the gear opens Settings', page is not None and page.parent is Window)
    if page is None:
        return
    settle(4)
    report('the page keeps clear of the camera, as the main screen does',
           page.children[0].padding[1] >= dp(10) + app._insets[0])

    sent = app.engine.settings_sent
    tap(page.at_once.buttons[5])
    report('a choice is saved at once, and the downloader told',
           app.settings.android_downloads_at_once == 5 and app.engine.settings_sent == sent + 1
           and phone.Settings.load().android_downloads_at_once == 5)
    tap(page.at_once.buttons[3])

    options = [button for row in page.body.children if hasattr(row, 'buttons')
               for button in row.buttons.values()]
    report('every choice has room for its whole word', labels_clear(options, room=BUTTON_MARGIN),
           f'{len(options)} choices')

    tap(page.check_button)
    wait_until(lambda: page.update_note.text not in ('', 'Checking…'), 3)
    report('Check for updates answers on the page',
           page.update_note.text == 'This is the newest version.' and not page.get_slot.children,
           page.update_note.text)
    apk = updates.Asset('Grabbit-9.0.0-arm64.apk', 1, '0' * 64, 'https://example.invalid/apk')
    page.answered(updates.Check(APP_VERSION, updates.Release('9.0.0', 'now', '', None, apk), apk))
    settle()
    report('and offers a newer one to get from there',
           page.get_button.parent is page.get_slot and '9.0.0' in page.get_button.text,
           page.get_button.text)

    report('with no phone, the battery setting says so rather than offering a button',
           not page.battery_slot.children, page.battery_note.text[:40])
    page.dismiss()
    wait_until(lambda: app.settings_page is None, 3)
    report('and ‹ closes it', app.settings_page is None)


class FakeHands:
    """Stands in for FileShare and Touch: what would have gone to Android."""

    def __init__(self):
        self.opened, self.shared, self.buzzes = [], [], 0

    def open(self, path):
        self.opened.append(path)
        return '' if os.path.isfile(path) else 'gone'

    def share(self, paths):
        self.shared.append(list(paths))
        return ''

    def held(self):
        self.buzzes += 1


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b'i%de' % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b'%d:%s' % (len(value), value)
    if isinstance(value, list):
        return b'l' + b''.join(map(bencode, value)) + b'e'
    return b'd' + b''.join(bencode(k) + bencode(v) for k, v in sorted(value.items())) + b'e'


def hold(widget, seconds: float = 0.8, wander: float = 0.0):
    """A finger resting on a widget - or, with wander, sliding along it."""
    settle()
    x, y = widget.to_window(*widget.center)
    touch = Tap('preview', 7, [x / Window.width, y / Window.height],
                is_touch=True, type_id='touch')
    EventLoop.post_dispatch_input('begin', touch)
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if wander:
            moved = wander * (time.monotonic() - started) / seconds
            touch.move([(x + moved) / Window.width, y / Window.height])
            EventLoop.post_dispatch_input('update', touch)
        EventLoop.idle()
        time.sleep(0.02)
    lift(touch)
    settle(1)


def check_finished(app, report):
    """A finished download opens with a tap and shares from its row; holding
    one picks it out, and the picked ones share or go together."""
    from kivy.metrics import dp
    from grabbit_mobile.ui.widgets import BUTTON_MARGIN, Dialog

    hands = app.hands = FakeHands()
    folder = tempfile.mkdtemp(prefix='grabbit-finished-')
    try:
        tasks = {task.name: task for task in app.engine.store}
        video = tasks['did you know there are two types of tiktok photo mode.mp4']
        photo = tasks['NASA - 01.jpg']
        torrent = tasks['ubuntu-24.04.3-desktop-amd64.iso']
        paused = tasks['Big Buck Bunny.mp3']
        for task in (video, photo):
            task.save_dir = folder
            task.file_path = os.path.join(folder, task.name)
            with open(task.file_path, 'wb') as handle:
                handle.write(b'x' * 64)
        # A torrent of three files, two of them chosen - and the third there
        # too, as aria2 leaves the edges of the files beside chosen ones.
        meta = {'info': {'name': 'Holiday', 'piece length': 16384, 'pieces': b'\0' * 20,
                         'files': [{'length': 64, 'path': ['clip.mp4']},
                                   {'length': 64, 'path': ['notes.txt']},
                                   {'length': 64, 'path': ['extras', 'photo.jpg']}]}}
        torrent.torrent_file = os.path.join(folder, 'holiday.torrent')
        with open(torrent.torrent_file, 'wb') as handle:
            handle.write(bencode(meta))
        torrent.save_dir, torrent.select_files, torrent.name = folder, '1,3', 'Holiday'
        chosen_files = [os.path.join(folder, 'Holiday', 'clip.mp4'),
                        os.path.join(folder, 'Holiday', 'extras', 'photo.jpg')]
        os.makedirs(os.path.join(folder, 'Holiday', 'extras'))
        for path in chosen_files + [os.path.join(folder, 'Holiday', 'notes.txt')]:
            with open(path, 'wb') as handle:
                handle.write(b'x' * 64)
        app.refresh()
        settle()

        rows = app._rows
        middles = {round(w.to_window(*w.center)[1] - row.heading.to_window(*row.heading.center)[1], 1)
                   for row in rows.values()
                   for w in (row.glyph, row.title, *row.heading.children)}
        report("every row's symbols and name sit on one middle line", middles == {0.0},
               str(sorted(middles)))
        from grabbit_mobile.ui import fonts
        primary = fonts.FONTS.regular
        runs = fonts.FONTS.runs('↓ 2 MB/s  Sgt. J 🇺🇸 - Superman', primary)
        report('a symbol the font lacks comes from a fallback, and emoji are left out',
               [path for path, _ in runs][:2] == [fonts.FONTS.chain[-1], primary]
               and '🇺' not in ''.join(piece for _, piece in runs),
               ' | '.join(f'{os.path.basename(path)}: {piece!r}' for path, piece in runs))
        with_share = sorted(task_id for task_id, row in rows.items()
                            if row.share_button.parent is row.heading)
        report('finished downloads carry a share button, and nothing else does',
               with_share == sorted([video.id, photo.id, torrent.id]),
               f'{len(with_share)} of {len(rows)} rows')

        reveal(rows[video.id].title)
        tap(rows[video.id].title)
        report("tapping a finished download's name opens its file",
               hands.opened[-1:] == [video.file_path], str(hands.opened[-1:]))
        reveal(rows[photo.id].detail)
        tap(rows[photo.id].detail)
        report('and so does its status line',
               hands.opened[-1:] == [photo.file_path], rows[photo.id].detail.text)
        reveal(rows[video.id].share_button)
        tap(rows[video.id].share_button)
        report('the share button shares that file', hands.shared[-1:] == [[video.file_path]],
               str(hands.shared[-1:]))

        count = len(hands.opened)
        reveal(rows[torrent.id].title)
        tap(rows[torrent.id].title)
        wait_until(lambda: app.details is not None and app.details.parent is Window, 3)
        settle(3)
        from grabbit_mobile.ui.details import FileLine
        lines = [w for w in reversed(app.details.rows.children) if isinstance(w, FileLine)]
        report('a torrent of several files opens the list of the ones chosen',
               app.details.tab == 'Files' and len(lines) == 2 and len(hands.opened) == count,
               f'{len(lines)} files')
        if lines:
            tap(lines[-1])
            report('and tapping one opens it', hands.opened[-1:] == chosen_files[-1:],
                   str(hands.opened[-1:]))
        app.details.dismiss()
        wait_until(lambda: app.details.parent is None, 3)

        # Picking out.
        before = paused.state
        reveal(rows[paused.id].detail)
        hold(rows[paused.id].detail)
        report('holding a download picks it out, with a buzz',
               app.choosing and app.chosen == {paused.id} and hands.buzzes == 1
               and app.choose_bar.parent is app.top_slot
               and app.choose_actions.parent is app.bottom_slot,
               f'{len(app.chosen)} picked')
        report('and the finger that held it is not also a tap',
               paused.state == before, f'{before} -> {paused.state}')
        report('its ring is ticked, and the buttons on the rows make way',
               rows[paused.id].glyph._kind == 'chosen' and rows[video.id].glyph._kind == 'unchosen'
               and all(row.details_button.parent is None for row in rows.values()))
        opened = len(hands.opened)
        reveal(rows[video.id].title)
        tap(rows[video.id].title)
        report('a tap then picks another, rather than opening it',
               app.chosen == {paused.id, video.id} and len(hands.opened) == opened
               and app.chosen_label.text == '2 selected', app.chosen_label.text)
        reveal(rows[video.id].detail)
        tap(rows[video.id].detail)
        report('and a second tap drops it again', app.chosen == {paused.id})

        tap(app.choose_all_button)
        report('Select all picks every download on show',
               app.chosen == set(rows) and app.choose_all_button.text == 'Deselect',
               f'{len(app.chosen)} of {len(rows)}')
        buttons = [app.choose_all_button, app.share_chosen_button, app.remove_chosen_button]
        report('every button while picking has room for its word',
               labels_clear(buttons, room=BUTTON_MARGIN), f'{Window.width}px wide')
        tap(app.share_chosen_button)
        report('Share shares every file the finished ones made, and stops picking',
               sorted(hands.shared[-1]) == sorted([video.file_path, photo.file_path] + chosen_files)
               and not app.choosing, f'{len(hands.shared[-1])} files')

        count = len(list(app.engine.store))
        reveal(rows[video.id].title)
        hold(rows[video.id].title)
        reveal(rows[photo.id].title)
        tap(rows[photo.id].title)
        tap(app.remove_chosen_button)
        settle(6)
        dialog = open_dialog()
        texts = [b.text for b in reversed(dialog.buttons.children)] if dialog else []
        report('Remove asks once, for all of them',
               dialog is not None and 'Delete them too' in texts and 'Keep the files' in texts,
               ' / '.join(texts))
        if dialog is not None:
            tap(next(b for b in dialog.buttons.children if b.text == 'Keep the files'))
            wait_until(lambda: open_dialog() is None, 3)
        report('and removes exactly those, and stops picking',
               len(list(app.engine.store)) == count - 2 and video.id not in rows
               and photo.id not in rows and not app.choosing,
               f'{count} -> {len(list(app.engine.store))}')

        reveal(rows[paused.id].title)
        hold(rows[paused.id].title)
        Window.dispatch('on_keyboard', 27, 0, None, [])
        settle()
        report('Back stops picking, and removes nothing',
               not app.choosing and paused.id in rows and all(
                   row.details_button.parent is row.heading for row in rows.values()))

        reveal(rows[paused.id].title)
        hold(rows[paused.id].title)
        reveal(app._status_chips['seeding'])
        tap(app._status_chips['seeding'])
        report('a filter that hides the picked ones stops picking',
               not app.choosing and app.status_filter == 'seeding')
        reveal(app._status_chips['all'])
        tap(app._status_chips['all'])

        reveal(rows[paused.id].title)
        hold(rows[paused.id].title, wander=dp(60))
        report('a finger sliding along a download does not pick it', not app.choosing)

        os.remove(chosen_files[0])
        os.remove(chosen_files[1])
        os.remove(os.path.join(folder, 'Holiday', 'notes.txt'))
        opened = len(hands.opened)
        reveal(rows[torrent.id].title)
        tap(rows[torrent.id].title)
        settle()
        report('a download whose files are gone says so, rather than opening nothing',
               len(hands.opened) == opened and 'moved or deleted' in app.message.text,
               app.message.text)
    finally:
        app.hands = None
        shutil.rmtree(folder, ignore_errors=True)


def check(app):
    """Tap through everything and report it, the way the other tests do."""
    results = []

    def report(name, ok, detail=''):
        results.append((name, ok))
        print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''))

    from grabbit_mobile.ui.widgets import BUTTON_MARGIN

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
    reveal(app._status_chips['paused'])
    tap(app._status_chips['paused'])
    report('the controls below it still take taps', app.status_filter == 'paused',
           app.status_filter)
    reveal(app._status_chips['all'])
    tap(app._status_chips['all'])
    tap(app.update_later)
    settle()
    report('Later takes it away entirely',
           not app.update_slot.children and app.update_slot.height == 0)
    app._on_update_checked(newer, False)
    settle()
    report('and keeps that version away until Grabbit starts again', not app.update_slot.children)
    app._on_update_checked(newer, True)
    settle()
    asked = app.update_banner.parent is app.update_slot
    tap(app.update_later)
    settle()
    report('though asking from the footer still shows it', asked)

    report('every launch asks for updates straight away', UPDATE_CHECKS == ['android'],
           f'{len(UPDATE_CHECKS)} check(s)')
    app.on_resume()
    wait_until(lambda: not app._update_checking, 3)
    soon = len(UPDATE_CHECKS)
    app._last_update_check -= phone.UPDATE_RECHECK + 1
    app.on_resume()
    wait_until(lambda: len(UPDATE_CHECKS) > soon, 3)
    report('opening it again asks again - but not twice within a minute',
           soon == 1 and len(UPDATE_CHECKS) == 2, f'{soon} then {len(UPDATE_CHECKS)} check(s)')

    from kivy.metrics import dp
    title = app.top_bar.children[-1]
    inset = title.to_window(title.x, 0)[0]
    report("the title sits in from the edge, in line with the link box's text",
           inset >= dp(16), f'{inset:.0f}px')
    report('and stays on one line, however narrow the phone',
           title.texture_size[1] < 1.6 * title.font_size and title.width >= title.texture_size[0],
           f'{Window.width}px wide')
    report('there are no format chips on the main screen any more',
           not hasattr(app, '_format_chips'))

    from grabbit_mobile.ui.linkbox import LinkBox
    report("the link box is the one that becomes Android's own field on a phone",
           isinstance(app.input, LinkBox), type(app.input).__name__)
    check_page(app, report)

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
    report('and every tab has room for its whole word', labels_clear(
        app.details.tab_chips.values(), room=BUTTON_MARGIN),
        ', '.join(f'{c.text} {c.width:.0f}px' for c in app.details.tab_chips.values()))
    app.details.dismiss()
    wait_until(lambda: app.details.parent is None)
    check_dialogs(app, report)
    check_settings(app, report)
    check_finished(app, report)

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


def show_page(app, kind: str, shot: str | None):
    """Open the page for the sample video with one type chosen; with --shot,
    save a picture of it once the thumbnail or frame is in."""
    site = SampleSite()
    app.engine.site = site
    app.inspect(site.url)
    page = app.page
    wait_until(lambda: page.analysis is not None, 10)
    page.type_row.choose(kind)
    if kind == 'image':
        page.scrubber.value = site.duration * 0.4
        page._want_frame(page.scrubber.value)
        wait_until(lambda: page._frame_texture is not None and page.picture.color[3] == 1, 40)
    else:
        wait_until(lambda: page.picture.opacity == 1, 10)
    if shot:
        settle()
        Window.screenshot(name=shot)
        site.close()
        app.stop()


# Every check for updates the app makes, answered by nothing_newer.
UPDATE_CHECKS = []


def nothing_newer(platform, current=None, timeout=15):
    """Stands in for asking GitHub, which the app now does the moment it is on
    screen: nothing newer, at once, and counted."""
    from grabbit import APP_VERSION, updates
    UPDATE_CHECKS.append(platform)
    return updates.Check(current or APP_VERSION)


def main():
    phone.RemoteEngine = FakeEngine
    phone.updates.check = nothing_newer
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
            # Newer than any real one, so the banner shows whatever this copy is.
            apk = updates.Asset('Grabbit-9.0.0-arm64.apk', 1, '0' * 64, updates.RELEASES_PAGE)
            sample = updates.Check(APP_VERSION, updates.Release('9.0.0', 'now', '', None, apk), apk)
            app.show_update(sample)
        if '--check' in sys.argv:
            code = check(app)
            app.stop()
            sys.exit(code)
        if '--page' in sys.argv:
            show_page(app, sys.argv[sys.argv.index('--page') + 1], shot)
            return
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
                elif '--choose' in sys.argv:
                    # Two downloads picked out, as holding one and tapping another leaves them.
                    ids = [t.id for t in app.engine.store]
                    app.start_choosing(ids[2])
                    app.toggle_chosen(ids[4])
                    Clock.schedule_once(
                        lambda _: (Window.screenshot(name=shot), app.stop()), 0.8)
                elif '--settings' in sys.argv:
                    app.open_settings()
                    Clock.schedule_once(
                        lambda _: (Window.screenshot(name=shot), app.stop()), 0.8)
                elif '--dialog' in sys.argv:
                    if sys.argv[sys.argv.index('--dialog') + 1] == 'storage':
                        app.ask_for_storage()
                    else:
                        app.confirm_remove(list(app.engine.store)[0].id)
                    Clock.schedule_once(
                        lambda _: (Window.screenshot(name=shot), app.stop()), 0.8)
                else:
                    app.stop()
            Clock.schedule_once(sheet, 0.6)

    Clock.schedule_once(ready, 0.4)
    app.run()


if __name__ == '__main__':
    main()
