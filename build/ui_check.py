"""Checks the desktop window's interface, without a screen or a network.

selftest.py exercises the engine against real downloads; this exercises the
window around it - the filters, the speed graph, the details tabs - with a few
invented tasks and Qt's offscreen platform, so it runs anywhere in seconds.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\ui_check.py
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from grabbit import paths as _paths                # noqa: E402

# Somewhere of its own: reading the real task list would make the counts below
# depend on whatever happens to be in it, and writing to it would leave
# invented downloads in the app.
_paths.set_data_dir(tempfile.mkdtemp(prefix='grabbit-ui-check-'))

from PySide6.QtCore import Qt                      # noqa: E402
from PySide6.QtWidgets import QApplication         # noqa: E402

from grabbit.engine import Engine                  # noqa: E402
from grabbit.settings import Settings              # noqa: E402
from grabbit.tasks import KIND_MEDIA, KIND_TORRENT, State, Task  # noqa: E402
from grabbit.ui import theme                       # noqa: E402

results = []


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''))


def sample(engine):
    folder = os.path.join(os.path.expanduser('~'), 'Downloads', 'Grabbit')
    for task in (
            Task(kind=KIND_MEDIA, source='https://youtu.be/jNQXAC9IVRw',
                 name='Me at the zoo.mp4', save_dir=folder, state=State.DOWNLOADING,
                 total=44_000_000, done=18_500_000, down_speed=3_900_000,
                 connections=8, media={'quality': 'best'}),
            Task(kind=KIND_TORRENT, source='magnet:?xt=urn:btih:abc',
                 name='ubuntu-24.04.iso', save_dir=folder, state=State.SEEDING,
                 total=5_170_000_000, done=5_170_000_000, up_speed=820_000,
                 seeds=4, connections=19, uploaded=2_400_000_000, info_hash='abc'),
            Task(kind=KIND_MEDIA, source='https://tiktok.com/x', name='clip.mp4',
                 save_dir=folder, state=State.COMPLETED, total=2_742_793,
                 done=2_742_793, media={'quality': 'best'})):
        engine.store.add(task)


def check_still(app):
    """A video's card offers one frame of it as a picture, chosen with a slider.

    The frames are real: frames_check.py's video - its brightness climbs
    steadily, so a frame's brightness says when it is from - served here the
    way a site serves one, and read the way the app reads any other.
    """
    import threading
    import time
    from http.server import ThreadingHTTPServer
    from pathlib import Path

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import frames_check
    from grabbit import analyze
    from grabbit.media import MediaItem, ProbeResult
    from grabbit.ui.add_dialog import MediaCard

    folder = Path(tempfile.mkdtemp(prefix='grabbit-still-'))
    frames_check.make_video(folder)
    server = ThreadingHTTPServer(('127.0.0.1', 0),
                                 lambda *a: frames_check.RangeHandler(*a, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f'http://127.0.0.1:{server.server_port}/climb.mp4'
    item = MediaItem(key='climb', kind='video', title='A video that gets brighter', url=url,
                     duration=float(frames_check.SECONDS), heights=[360])
    probe = ProbeResult(url=url, title=item.title, items=[item], heights=[360])
    card = MediaCard(analyze.Analysis(url=url, kind=analyze.KIND_MEDIA, probe=probe), Settings())

    def wait(condition, seconds):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline and not condition():
            app.processEvents()
            time.sleep(0.02)
        return condition()

    def shown_time():
        image = card.frame_picker.picture.pixmap().toImage()
        reds = [image.pixelColor(x, y).red() for x in range(20, image.width() - 20, 23)
                for y in range(20, image.height() - 20, 17)]
        return (sum(reds) / max(1, len(reds))) * 219 / 255 / frames_check.RATE

    try:
        kinds = [card.kind.itemData(i) for i in range(card.kind.count())]
        report("a video's card offers a still from it", 'image' in kinds, ', '.join(kinds))
        card.kind.setCurrentIndex(kinds.index('image'))
        picker = card.frame_picker
        report('choosing it shows a slider along the video, and picture formats',
               picker is not None and picker.isVisibleTo(card)
               and picker.slider.maximum() == frames_check.SECONDS * 1000
               and card.format.findData('jpg') >= 0)
        first = wait(lambda: not picker.picture.pixmap().isNull(), 40)
        report('and the frame at the start', first and shown_time() < 1.0,
               f'{shown_time():.1f}s' if first else picker.picture.text())
        picker.slider.setValue(12_000)
        report('the clock follows the slider', picker.clock.text().startswith('0:12.0'),
               picker.clock.text())
        moved = wait(lambda: abs(shown_time() - 12.0) < 1.0, 30)
        report('and the frame there is shown once it stops', moved, f'{shown_time():.1f}s')
        card.format.setCurrentIndex(card.format.findData('jpg'))
        request = card.requests()[0]
        report('Download asks for that frame, as that kind of picture',
               request['quality'] == 'frame' and request['frame_at'] == 12.0
               and request['frame_format'] == 'jpg',
               f"{request['quality']} at {request.get('frame_at')}s as {request.get('frame_format')}")
    finally:
        card.shutdown()
        server.shutdown()


def check_choices(app):
    """Type, then format, then quality - from what the source has."""
    from grabbit import analyze
    from grabbit.media import MediaItem, ProbeResult
    from grabbit.ui.add_dialog import MediaCard

    def card_for(formats, title):
        item = MediaItem(key=title, kind='video', title=title, url='https://example.invalid/x',
                         info={'formats': formats})
        probe = ProbeResult(url=item.url, title=title, items=[item])
        settings = Settings()
        return MediaCard(analyze.Analysis(url=item.url, kind=analyze.KIND_MEDIA, probe=probe),
                         settings)

    def menu(combo):
        return [(combo.itemData(i), combo.itemText(i)) for i in range(combo.count())]

    # Formats as the probe leaves them: in yt-dlp's order, worst first.
    track = card_for([
        {'format_id': 'hls_opus_64', 'vcodec': 'none', 'acodec': 'opus', 'abr': 64, 'asr': 48000,
         'ext': 'opus'},
        {'format_id': 'http_mp3_128', 'vcodec': 'none', 'acodec': 'mp3', 'abr': 128, 'asr': 44100,
         'ext': 'mp3'}], 'A track')
    report('a track with nothing to see is offered as audio alone',
           [v for v, _ in menu(track.kind)] == ['audio'], str(menu(track.kind)))
    formats = [v for v, _ in menu(track.format)]
    report('in every audio format, FLAC and WAV among them',
           formats == ['mp3', 'm4a', 'opus', 'flac', 'wav', 'best'], ', '.join(formats))
    track.format.setCurrentIndex(track.format.findData('flac'))
    rates = menu(track.quality)
    report("FLAC's qualities are sample rates, the first the source's, saying what it is",
           rates[0] == ('', 'Source (MP3 128 kbps · 44.1 kHz)') and ('32000', '32 kHz') in rates
           and all(int(v) < 44100 for v, _ in rates[1:]), ' | '.join(t for _, t in rates))
    track.quality.setCurrentIndex(track.quality.findData('32000'))
    request = track.requests()[0]
    report('and Download asks for FLAC at that rate, from the sound it named',
           request['quality'] == 'audio_flac'
           and request['options'] == {'audio_format': 'http_mp3_128', 'sample_rate': 32000},
           f"{request['quality']} {request['options']}")
    track.format.setCurrentIndex(track.format.findData('mp3'))
    bitrates = menu(track.quality)
    report("MP3's are bitrates, none past the source's, which the first names",
           [v for v, _ in bitrates] == ['0', '128', '96', '64']
           and bitrates[0][1] == 'Highest (source: MP3 128 kbps)',
           ' | '.join(t for _, t in bitrates))
    track.format.setCurrentIndex(track.format.findData('best'))
    original = menu(track.quality)
    report("Original lists the site's own sounds, the source's best first, each the one it names",
           original[0] == ('id:http_mp3_128', 'Source (MP3 128 kbps · 44.1 kHz)')
           and original[1] == ('id:hls_opus_64', 'Opus 64 kbps · 48 kHz'),
           ' | '.join(t for _, t in original))
    track.quality.setCurrentIndex(1)
    report('and Download keeps the one chosen, unconverted',
           track.requests()[0]['options'] == {'audio_format': 'hls_opus_64'})

    video = card_for([
        {'format_id': '401', 'vcodec': 'av01', 'acodec': 'none', 'width': 3840, 'height': 2160,
         'ext': 'mp4'},
        {'format_id': '137', 'vcodec': 'avc1', 'acodec': 'none', 'width': 1920, 'height': 1080,
         'fps': 60, 'ext': 'mp4'},
        {'format_id': '248', 'vcodec': 'vp9', 'acodec': 'none', 'width': 1920, 'height': 1080,
         'fps': 30, 'ext': 'webm'},
        {'format_id': '140', 'vcodec': 'none', 'acodec': 'mp4a.40.2', 'abr': 129, 'asr': 44100,
         'ext': 'm4a'},
        {'format_id': '251', 'vcodec': 'none', 'acodec': 'opus', 'abr': 135, 'asr': 48000,
         'ext': 'webm'}], 'A video')
    report('a video can be a video, its sound, a GIF or one picture',
           [v for v, _ in menu(video.kind)] == ['video', 'audio', 'gif', 'image'])
    report('its formats include WebM, as the site has WebM',
           [v for v, _ in menu(video.format)] == ['mp4', 'mkv', 'webm', 'any'])
    sizes = [t for _, t in menu(video.quality)]
    report('and its qualities are its own sizes, the first the source, saying what it is',
           sizes == ['Source (2160p · 3840×2160 · 4K)', '1080p60 · 1920×1080'],
           ' | '.join(sizes))
    video.kind.setCurrentIndex(video.kind.findData('audio'))
    video.format.setCurrentIndex(video.format.findData('mp3'))
    first = menu(video.quality)[0][1]
    mp3 = video.requests()[0]['options']
    video.format.setCurrentIndex(video.format.findData('m4a'))
    m4a_first = menu(video.quality)[0][1]
    m4a = video.requests()[0]['options']
    report('its sound becomes MP3 from the best one, M4A from the one already AAC',
           first == 'Highest (source: Opus 135 kbps)' and mp3.get('audio_format') == '251'
           and m4a_first == 'Highest (source: AAC 129 kbps)' and m4a.get('audio_format') == '140',
           f'{first} {mp3} | {m4a_first} {m4a}')
    video.kind.setCurrentIndex(video.kind.findData('video'))
    video.quality.setCurrentIndex(video.quality.findData('1080'))
    video.format.setCurrentIndex(video.format.findData('mkv'))
    request = video.requests()[0]
    report('Download asks for that size, in that format',
           request['quality'] == '1080' and request['container'] == 'mkv' and not request['options'],
           f"{request['quality']} {request['container']}")
    tall = card_for([{'format_id': 'h', 'vcodec': 'h264', 'acodec': 'aac', 'width': 1080,
                      'height': 1920, 'ext': 'mp4'}], 'A phone video')
    report("a phone's tall video is called by its shorter side",
           menu(tall.quality) == [('best', 'Source (1080p · 1080×1920)')], str(menu(tall.quality)))
    report('and without WebM to be had, no WebM is offered',
           'webm' not in [v for v, _ in menu(tall.format)])
    for card in (track, video, tall):
        card.shutdown()


def main():
    app = QApplication([])
    theme.install(app)
    theme.apply_theme(app, 'dark')

    from grabbit import APP_VERSION, updates
    from grabbit.ui.main_window import MainWindow

    # The window asks GitHub for a newer Grabbit the moment it is up. Here it
    # is told there is none, at once: the checks below feed it their own
    # answers, and must not race a real one still on its way back.
    launched = []

    def nothing_newer(platform, current=None, timeout=15):
        launched.append(platform)
        return updates.Check(current or APP_VERSION)

    updates.check = nothing_newer

    settings = Settings()
    settings.show_graph = False
    engine = Engine(settings)
    sample(engine)
    window = MainWindow(engine, settings)
    window.resize(1180, 760)
    window.model._ids = [t.id for t in engine.store]
    window.model.layoutChanged.emit()
    window.show()
    app.processEvents()

    report('the window builds with downloads in it',
           window.proxy.rowCount() == 3, f'{window.proxy.rowCount()} rows')

    report('the graph starts hidden', not window.graph.isVisible())

    window._on_sidebar_clicked(window.graph_item)
    app.processEvents()
    sizes = window.vertical_splitter.sizes()
    share = sizes[1] / max(1, sum(sizes))
    report('the sidebar entry shows the graph', window.graph.isVisible(),
           f'{window.graph_item.text()} is {"checked" if window.graph_item.checkState() == Qt.Checked else "unchecked"}')
    report('it opens at about half the height', 0.3 < share < 0.6, f'{share:.0%} of the pane')

    window.graph.push(4_000_000, 900_000)
    window.graph.push_tasks(engine.store)
    window.graph.repaint()
    report('the graph draws the totals', window.graph._titles()[0] == 'All downloads',
           window.graph._titles()[1])

    first = next(iter(engine.store))
    window.view.setCurrentIndex(window.proxy.index(0, 0))
    window._on_selection_changed()
    window.graph.repaint()
    caption = window.graph._titles()[0]
    report('selecting a download switches the graph to it',
           caption not in ('All downloads', ''), caption)

    # Its own history, not a copy of the totals.
    history = window.graph._history.get(window.graph._task_id)
    report('each download keeps its own history', history is not None
           and history is not window.graph._history[''],
           f'{len(history.down)} samples' if history else 'none')

    window.details.set_task(first.id)
    for index in range(window.details.count()):
        window.details.setCurrentIndex(index)
        window.details.refresh(force=True)
        app.processEvents()
    report('every details tab refreshes', True,
           ', '.join(window.details.tabText(i) for i in range(window.details.count())))

    for key in ('completed', 'downloading', 'all'):
        window.proxy.set_status(key)
    window.proxy.set_kind('torrent')
    torrents = window.proxy.rowCount()
    window.proxy.set_kind('all')
    report('the filters narrow the list', torrents == 1, f'{torrents} torrent(s)')

    window._on_sidebar_clicked(window.graph_item)
    app.processEvents()
    report('the sidebar entry hides it again', not window.graph.isVisible())

    window._tick()
    report('the once-a-second refresh runs', True, window.count_label.text().strip())

    # Updates, fed answers directly: build/update_check.py covers asking GitHub
    # and build/selfupdate_check.py the installing.
    from PySide6.QtCore import QTimer
    from grabbit import selfupdate, updates
    menus = {a.text(): a.menu() for a in window.menuBar().actions()}
    report('File and Help can both check for updates',
           window.action_check_updates in menus['&File'].actions()
           and window.action_check_updates in menus['&Help'].actions())
    report('and the window asked by itself the moment it opened', launched == ['windows'],
           f'{len(launched)} check(s)')

    zip_asset = updates.Asset('Grabbit-9.0.0-win64.zip', 1, '0' * 64, 'https://example.invalid/zip')
    newer = updates.Check('1.2.0', updates.Release('9.0.0', 'now', 'What changed.', zip_asset, None),
                          zip_asset)
    window._on_update_checked(updates.Check('1.2.0', error='offline'), False)
    report('an automatic check with nothing to offer stays quiet',
           window.update_banner.isHidden())

    # The question is modal, so answer it the way a person would.
    asked = []

    def answer_later():
        box = window._update_question
        if box is None:
            QTimer.singleShot(50, answer_later)
            return
        asked.append(box.text())
        next(b for b in box.buttons() if b.text() == 'Later').click()

    QTimer.singleShot(50, answer_later)
    window._on_update_checked(newer, False)
    report('a check that finds one asks before updating', asked and '9.0.0' in asked[0],
           asked[0] if asked else 'never asked')
    report('and leaves the banner up after "Later"',
           window.update_banner.isVisibleTo(window) and '9.0.0' in window.update_label.text(),
           window.update_label.text())
    asked.clear()
    window._on_update_checked(newer, False)
    report('"Later" is not asked again this session', not asked)

    report('running from source says it cannot replace itself',
           'source' in (selfupdate.blocker() or ''), selfupdate.blocker())

    window.settings.check_for_updates = False
    window.check_for_updates(False)
    report('the setting turns the automatic checks off', not window._update_checking)
    window.settings.check_for_updates = True

    # The icon is baked into the exe at build time, when the palette is just
    # Windows' accent of the day, so it must not take its colour from there.
    # 1.2.0 was built under a gold accent and shipped a gold icon: recreate that
    # palette and check the icon ignores it.
    from PySide6.QtGui import QColor, QPalette
    from grabbit.ui import icons
    gold = QPalette(app.palette())
    gold.setColor(QPalette.Highlight, QColor('#9c6f00'))
    app.setPalette(gold)
    image = icons.render_app_icon(64).toImage()
    tile = image.pixelColor(12, 32).name()
    report('the app icon keeps its own colour under any accent',
           tile == icons.APP_ICON_COLOR, f'{tile} under a {app.palette().highlight().color().name()} accent')

    # And the suite's shape: the tile runs to the edge, like Homework Hub's,
    # with its corners rounded away.
    edge, corner = image.pixelColor(0, 32), image.pixelColor(0, 0)
    report('the app icon fills its square, corners rounded',
           edge.name() == icons.APP_ICON_COLOR and edge.alpha() == 255 and corner.alpha() == 0,
           f'edge {edge.name()} alpha {edge.alpha()}, corner alpha {corner.alpha()}')

    # Qt hides a single & in a tab's name - it marks the key that picks the
    # tab - so a name meant to show one has to be written with two.
    from PySide6.QtWidgets import QTabWidget
    from grabbit.ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog(engine.settings, window)
    tabs = dialog.findChild(QTabWidget)
    names = [tabs.tabText(i) for i in range(tabs.count())]
    shown = [name.replace('&&', '\0').replace('&', '').replace('\0', '&') for name in names]
    report('every Options tab shows its whole name, & included',
           'Videos & Photos' in shown and all('&' not in n.replace('&&', '') for n in names),
           ' | '.join(shown))
    dialog.deleteLater()

    # Watch, with no mpv or VLC: a Store app - Windows 11's Media Player -
    # cannot reach the stream server, so a stream goes to the desktop Windows
    # Media Player instead; a file still opens in whatever opens its kind.
    from unittest import mock
    from grabbit import player
    stream, file = 'http://127.0.0.1:1/token/clip.mp4', r'C:\clips\clip.mp4'
    with mock.patch.object(player, 'find_player', return_value=None), \
            mock.patch.object(player.os.path, 'isfile', return_value=True), \
            mock.patch.object(player.subprocess, 'Popen') as launched, \
            mock.patch.object(player.os, 'startfile', create=True) as opened:
        with mock.patch.object(player, '_store_app_opens', return_value=True):
            store_stream = player.play(stream, 'Clip')
            store_file = player.play(file, 'Clip')
        with mock.patch.object(player, '_store_app_opens', return_value=False):
            desktop_stream = player.play(stream, 'Clip')
    report('Watch sends a stream past a Store app to the desktop Media Player',
           store_stream == (True, 'Windows Media Player')
           and launched.call_args_list[0].args[0] == [player.LEGACY_PLAYER, stream], str(store_stream))
    report('and a file on disk to whatever opens its kind, a playlist only for streams',
           store_file == (True, 'default player') and opened.call_args_list[0].args[0] == file
           and opened.call_args_list[1].args[0].endswith('.m3u') and desktop_stream[0],
           ', '.join(str(c.args[0])[-20:] for c in opened.call_args_list))

    check_choices(app)
    check_still(app)

    engine.shutdown()
    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
