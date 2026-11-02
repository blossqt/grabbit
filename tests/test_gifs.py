"""A GIF's frames saved as pictures (gifs.py, ui\\gifpicker.py, the engine's add_gif_frames and
the add dialog's GifCard).

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe tests\\test_gifs.py

The GIF is drawn here with Pillow and served by a web server of the test's own on 127.0.0.1.
Its second frame changes only a corner, as most GIFs' frames do, and each has a
see-through square.
"""

import io
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PIL import Image  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from grabbit import gifs  # noqa: E402

APP = QApplication.instance() or QApplication([])

RED, BLUE = (220, 30, 40), (30, 60, 220)


def make_gif() -> bytes:
    first = Image.new('RGBA', (40, 30), RED + (255,))
    first.paste((0, 0, 0, 0), (30, 20, 40, 30))       # a see-through square
    second = first.copy()
    second.paste(BLUE + (255,), (0, 0, 10, 10))       # only the top left corner changes
    third = first.copy()
    out = io.BytesIO()
    first.save(out, 'GIF', save_all=True, append_images=[second, third],
               duration=100, loop=0, disposal=1)
    return out.getvalue()


GIF = make_gif()


class Site(BaseHTTPRequestHandler):
    def do_GET(self):
        # /guarded.gif is served only to its own site's pages, as Tenor's are.
        if self.path == '/guarded.gif' and self.headers.get('Referer') != 'https://tenor.example/':
            self.send_error(403)
            return
        if self.path not in ('/funny.gif', '/guarded.gif'):
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header('Content-Type', 'image/gif')
        self.send_header('Content-Length', str(len(GIF)))
        self.end_headers()
        self.wfile.write(GIF)

    def log_message(self, *args):
        pass


def colour(image: QImage, x: int, y: int) -> tuple:
    pixel = image.pixelColor(x, y)
    return pixel.red(), pixel.green(), pixel.blue()


def near(found, wanted) -> bool:
    return all(abs(a - b) < 12 for a, b in zip(found, wanted))


class WithSite(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.url = f'http://127.0.0.1:{cls.server.server_address[1]}/funny.gif'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.folder = tempfile.mkdtemp(prefix='grabbit-gif-')
        self.addCleanup(shutil.rmtree, self.folder, True)


class FrameTests(WithSite):
    def test_each_frame_whole(self):
        found = list(gifs.frames(GIF))
        self.assertEqual(len(found), 3)
        self.assertTrue(all(image.size().toTuple() == (40, 30) for image in found))
        self.assertTrue(near(colour(found[0], 2, 2), RED))
        self.assertTrue(near(colour(found[1], 2, 2), BLUE))
        self.assertTrue(near(colour(found[1], 20, 15), RED), 'the rest of the frame stays')

    def test_not_a_gif(self):
        with self.assertRaises(gifs.GifError):
            list(gifs.frames(b'<html>no</html>'))

    def test_is_gif(self):
        self.assertTrue(gifs.is_gif('image/gif'))
        self.assertTrue(gifs.is_gif('', 'Funny.GIF'))
        self.assertFalse(gifs.is_gif('image/jpeg', 'a.jpg'))

    def test_every_frame_into_a_folder(self):
        made = gifs.save_frames(GIF, self.folder, 'funny', gifs.ALL, 'png')
        self.assertEqual(os.path.basename(made), 'funny frames')
        self.assertEqual(sorted(os.listdir(made)),
                         ['funny - frame 1.png', 'funny - frame 2.png', 'funny - frame 3.png'])
        saved = QImage(os.path.join(made, 'funny - frame 2.png'))
        self.assertTrue(near(colour(saved, 2, 2), BLUE))
        self.assertTrue(near(colour(saved, 20, 15), RED))
        see_through = QImage(os.path.join(made, 'funny - frame 1.png'))
        self.assertEqual(see_through.pixelColor(35, 25).alpha(), 0, 'a PNG keeps what is see-through')
        # Again: a folder of its own, not mixed into the first.
        self.assertEqual(os.path.basename(gifs.save_frames(GIF, self.folder, 'funny', gifs.ALL, 'png')),
                         'funny frames (2)')

    def test_one_frame(self):
        made = gifs.save_frames(GIF, self.folder, 'funny', 1, 'png')
        self.assertEqual(os.listdir(self.folder), ['funny - frame 2.png'])
        self.assertTrue(near(colour(QImage(made), 2, 2), BLUE))

    def test_jpg_turns_see_through_white(self):
        made = gifs.save_frames(GIF, self.folder, 'funny', 0, 'jpg')
        self.assertTrue(made.endswith('funny - frame 1.jpg'))
        self.assertTrue(near(colour(QImage(made), 35, 25), (255, 255, 255)))

    def test_numbers_sort(self):
        self.assertEqual(gifs.frame_name('a', 6, 120, 'png'), 'a - frame 007.png')
        self.assertEqual(gifs.frame_name('a: b', 0, 9, 'jpg'), 'a_ b - frame 1.jpg')

    def test_fetch(self):
        seen = []
        self.assertEqual(gifs.fetch(self.url, progress=lambda done, total: seen.append((done, total))), GIF)
        self.assertEqual(seen[-1], (len(GIF), len(GIF)))
        with self.assertRaises(gifs.GifError):
            gifs.fetch(self.url.replace('funny', 'gone'))


def wait(test, until, seconds=10):
    end = time.perf_counter() + seconds
    while not until() and time.perf_counter() < end:
        APP.processEvents()
        time.sleep(0.01)
    test.assertTrue(until(), 'timed out')


class EngineTests(WithSite):
    def engine(self):
        from grabbit import engine as engine_mod
        data = Path(self.folder) / 'data'
        data.mkdir()
        patch = mock.patch('grabbit.tasks.data_dir', return_value=data)
        patch.start()
        self.addCleanup(patch.stop)
        settings = SimpleNamespace(download_dir=self.folder, max_media_jobs=2, site_subfolders=False)
        return engine_mod.Engine(settings)

    def test_every_frame(self):
        from grabbit.tasks import State
        engine = self.engine()
        task = engine.add_gif_frames(self.url, gifs.ALL, 'png', self.folder)
        self.assertEqual(task.name, 'funny frames')
        wait(self, lambda: task.state in (State.COMPLETED, State.ERROR))
        self.assertEqual(task.state, State.COMPLETED, task.error)
        self.assertEqual(task.file_path, os.path.join(self.folder, 'funny frames'))
        self.assertEqual(len(os.listdir(task.file_path)), 3)

    def test_one_frame_as_jpg(self):
        from grabbit.tasks import State
        engine = self.engine()
        task = engine.add_gif_frames(self.url, 0, 'jpg', self.folder, 'Cat')
        wait(self, lambda: task.state in (State.COMPLETED, State.ERROR))
        self.assertEqual(task.state, State.COMPLETED, task.error)
        self.assertEqual(os.path.basename(task.file_path), 'Cat - frame 1.jpg')

    def test_a_link_that_is_gone(self):
        from grabbit.tasks import State
        engine = self.engine()
        task = engine.add_gif_frames(self.url.replace('funny', 'gone'), gifs.ALL, 'png', self.folder)
        wait(self, lambda: task.state in (State.COMPLETED, State.ERROR))
        self.assertEqual(task.state, State.ERROR)
        self.assertIn('404', task.error)


class CardTests(WithSite):
    def card(self):
        from grabbit import analyze
        from grabbit.ui.add_dialog import GifCard
        analysis = analyze.Analysis(url=self.url, filename='funny.gif', content_type='image/gif',
                                    filesize=len(GIF))
        return GifCard(analysis, SimpleNamespace(frame_format='jpg'))

    def test_the_gif_itself_by_default(self):
        card = self.card()
        self.assertEqual(card.requests(), [{'type': 'file', 'url': self.url, 'name': 'funny.gif'}])
        self.assertFalse(card.format.isEnabled())

    def test_one_frame_chosen(self):
        card = self.card()
        card.save.setCurrentIndex(card.save.findData('frame'))
        wait(self, lambda: len(card.picker.shown) == 3)
        self.assertEqual(card.picker.count.text(), 'Frame 1 of 3')
        card.picker.step(1)
        self.assertEqual(card.picker.count.text(), 'Frame 2 of 3')
        self.assertEqual(card.requests(), [{'type': 'gif_frames', 'url': self.url, 'title': 'funny',
                                            'which': 1, 'frame_format': 'jpg'}])

    def test_every_frame(self):
        card = self.card()
        card.save.setCurrentIndex(card.save.findData('all'))
        card.format.setCurrentIndex(card.format.findData('png'))
        card.name_edit.setText('cat.gif')
        self.assertIsNone(card.picker, 'the GIF is only read to choose a frame')
        self.assertIn('cat frames', card.where.text())
        self.assertEqual(card.requests()[0]['which'], gifs.ALL)
        self.assertEqual(card.requests()[0]['title'], 'cat')

    def test_dialog_picks_the_gif_card(self):
        from grabbit import analyze
        from grabbit.ui import add_dialog
        dialog = add_dialog.AddDialog.__new__(add_dialog.AddDialog)
        dialog.settings = SimpleNamespace(frame_format='png')
        gif = analyze.Analysis(url=self.url, kind=analyze.KIND_FILE, filename='funny.gif',
                               content_type='image/gif')
        other = analyze.Analysis(url=self.url, kind=analyze.KIND_FILE, filename='a.zip',
                                 content_type='application/zip')
        self.assertIsInstance(add_dialog.AddDialog._card_for(dialog, gif), add_dialog.GifCard)
        self.assertNotIsInstance(add_dialog.AddDialog._card_for(dialog, other), add_dialog.GifCard)


class PageOfOneGifTests(WithSite):
    """A page whose one picture is a GIF (Tenor's): the GIF's card, not a gallery's."""

    def analysis(self, *items):
        from grabbit import analyze
        from grabbit.mediaitems import ProbeResult
        return analyze.Analysis(url='https://tenor.example/view/funny-gif-1', kind=analyze.KIND_GALLERY,
                                probe=ProbeResult(url='https://tenor.example/view/funny-gif-1',
                                                  kind='gallery', site='Tenor', title='Funny',
                                                  items=list(items)))

    def item(self, url=None, ext='gif'):
        from grabbit.mediaitems import MediaItem
        return MediaItem(kind='image', ext=ext, filename='funny.gif', width=40, height=30,
                         direct_url=url or self.url.replace('funny', 'guarded'),
                         headers={'Referer': 'https://tenor.example/'})

    def card_for(self, analysis):
        from grabbit.ui import add_dialog
        dialog = add_dialog.AddDialog.__new__(add_dialog.AddDialog)
        dialog.settings = SimpleNamespace(frame_format='png', video_quality='best',
                                          video_container='mp4')
        return add_dialog.AddDialog._card_for(dialog, analysis)

    def test_its_frames(self):
        from grabbit.ui.add_dialog import GifCard
        analysis = self.analysis(self.item())
        card = self.card_for(analysis)
        self.assertIsInstance(card, GifCard)
        self.assertEqual(card.url, analysis.url, "the card stays the page's, for removing it")
        self.assertEqual(card.title.text(), 'Funny')
        card.save.setCurrentIndex(card.save.findData('frame'))
        wait(self, lambda: len(card.picker.shown) == 3 or not card.picker.picture.text().startswith('Reading'))
        self.assertEqual(len(card.picker.shown), 3, card.picker.picture.text())
        card.picker.step(1)
        self.assertEqual(card.requests(), [{'type': 'gif_frames', 'url': self.item().direct_url,
                                            'title': 'funny', 'which': 1, 'frame_format': 'png',
                                            'headers': {'Referer': 'https://tenor.example/'}}])

    def test_the_gif_itself_as_before(self):
        analysis = self.analysis(self.item())
        request = self.card_for(analysis).requests()[0]
        self.assertEqual(request['type'], 'image')
        self.assertIs(request['probe'], analysis.probe)

    def test_the_site_sees_its_own_page(self):
        from grabbit.tasks import State
        engine = EngineTests.engine(self)
        task = engine.add_gif_frames(self.item().direct_url, gifs.ALL, 'png', self.folder, 'funny',
                                     {'Referer': 'https://tenor.example/'})
        wait(self, lambda: task.state in (State.COMPLETED, State.ERROR))
        self.assertEqual(task.state, State.COMPLETED, task.error)

    def test_still_a_gallery(self):
        from grabbit.ui.add_dialog import GalleryCard
        for analysis in (self.analysis(self.item(), self.item()),                  # two of them
                         self.analysis(self.item(self.url.replace('.gif', '.jpg'), 'jpg'))):
            with self.subTest(len(analysis.probe.items)):
                self.assertIsInstance(self.card_for(analysis), GalleryCard)


if __name__ == '__main__':
    unittest.main(verbosity=2)
