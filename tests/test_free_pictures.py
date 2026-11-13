"""Pictures as Grabbit finds them: free ones offered in place of a paid picture or a page behind a
robot check (analyze._free_instead), and every picture an ordinary page has, extension or not, at
its full size (pagescrape).

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe tests\\test_free_pictures.py

Openverse and Wikimedia Commons are stood in (suiteweb.free_matches); pages come from a web
server of the test's own on 127.0.0.1.
"""

import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from grabbit import analyze, pagescrape, suiteweb  # noqa: E402
from grabbit.settings import Settings  # noqa: E402

MATCHES = [
    {'title': 'Rock spire', 'url': 'https://upload.wikimedia.org/a/Rock_spire.jpg',
     'thumbnail': 'https://upload.wikimedia.org/t/a.jpg', 'licence': 'CC0', 'creator': 'Ann',
     'page': '', 'width': 4000, 'height': 3000},
    {'title': '', 'url': 'https://live.staticflickr.com/1/2_3_b.png', 'thumbnail': 'https://x/t',
     'licence': 'CC BY 2.0', 'creator': '', 'page': '', 'width': 1024, 'height': 683},
]


class FreeInsteadTests(unittest.TestCase):
    def test_a_paid_picture_is_not_fetched_and_free_ones_are_offered(self):
        url = 'https://www.shutterstock.com/image-photo/rock-spire-night-2155566'
        with mock.patch.object(suiteweb, 'free_matches', return_value=MATCHES) as asked, \
                mock.patch.object(analyze, 'http_head', side_effect=AssertionError('fetched')):
            found = analyze.analyze(url, Settings())
        asked.assert_called_once_with('rock spire night')
        self.assertEqual(found.kind, analyze.KIND_GALLERY)
        self.assertIn('Shutterstock pictures require a paid licence and are not downloaded.', found.hint)
        items = found.probe.items
        self.assertEqual([i.direct_url for i in items], [m['url'] for m in MATCHES])
        self.assertTrue(all(i.optional for i in items))                 # none chosen until picked
        self.assertEqual(items[0].title, 'Rock spire by Ann · CC0')
        self.assertEqual(items[0].filename, 'Rock spire by Ann.jpg')
        self.assertEqual((items[1].filename, items[1].ext), ('Untitled.png', 'png'))

    def test_a_paid_picture_with_nothing_like_it(self):
        with mock.patch.object(suiteweb, 'free_matches', return_value=[]):
            found = analyze.analyze('https://plus.unsplash.com/premium_photo-1661954372617?w=600', Settings())
        self.assertEqual(found.kind, analyze.KIND_ERROR)
        self.assertEqual(found.error, 'Unsplash+ pictures require a paid licence and are not downloaded.')

    def test_a_page_behind_a_robot_check(self):
        url = 'https://unsplash.com/photos/a-solitary-spire-under-a-starry-night-sky-VKdU_oP54uo'
        with mock.patch.object(analyze, 'matching_extractor', return_value=''), \
                mock.patch.object(analyze, '_try_gallery_dl', return_value=None), \
                mock.patch.object(analyze, 'http_head', return_value={'status': 401, 'robot': True}), \
                mock.patch.object(suiteweb, 'free_matches', return_value=MATCHES) as asked:
            found = analyze.analyze(url, Settings())
        asked.assert_called_once_with('solitary spire under starry night sky')
        self.assertTrue(found.robot)
        self.assertEqual(found.kind, analyze.KIND_GALLERY)
        self.assertIn('requires a browser check', found.hint)
        self.assertIn('Copy image address', found.hint)


PNG = b'\x89PNG\r\n\x1a\n' + b'\0' * 64


class Site(BaseHTTPRequestHandler):
    page = ''
    files = {}                          # path -> (type, size)

    def _answer(self, body):
        path = self.path.split('?')[0]
        if path == '/page':
            data = Site.page.encode()
            kind = 'text/html; charset=utf-8'
        elif path in Site.files:
            kind, size = Site.files[path]
            data = PNG[:1] * size
        else:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if body:
            self.wfile.write(data)

    def do_GET(self):
        self._answer(True)

    def do_HEAD(self):
        self._answer(False)

    def log_message(self, *args):
        pass


class ScrapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()

    def test_pictures_without_an_extension_json_ld_and_css(self):
        base = self.base
        Site.files = {'/cdn/abc': ('image/webp', 5_000), '/cdn/page-not-picture': ('text/html', 100),
                      '/ld/photo': ('image/jpeg', 7_000), '/bg/hero': ('image/png', 9_000),
                      '/files/sea-300x200.jpg': ('image/jpeg', 2_000), '/files/sea.jpg': ('image/jpeg', 60_000),
                      '/img/rose.jpeg': ('image/jpeg', 4_000)}
        Site.page = f'''<html><head><title>Holiday</title>
            <script type="application/ld+json">{{"@type": "ImageObject", "contentUrl": "{base}/ld/photo"}}</script>
            </head><body>
            <img src="/cdn/abc" alt="">
            <img src="/cdn/page-not-picture">
            <div style="background-image: url('/bg/hero')"></div>
            <img src="/files/sea-300x200.jpg" srcset="/files/sea-300x200.jpg 300w, /files/sea.jpg 1200w">
            <img src="/img/rose.jpeg">
            <img src="/static/logo.svg">
            </body></html>'''
        found = pagescrape.scrape(f'{base}/page')
        self.assertFalse(found.error)
        by_name = {item.filename: item.direct_url for item in found.items}
        self.assertEqual(by_name, {
            'abc.webp': f'{base}/cdn/abc',               # asked: the server says webp
            'photo.jpg': f'{base}/ld/photo',             # JSON-LD
            'hero.png': f'{base}/bg/hero',               # a CSS background
            'sea.jpg': f'{base}/files/sea.jpg',          # the small copy and srcset's large one: one picture
            'rose.jpeg': f'{base}/img/rose.jpeg',        # no second extension
        })

    def test_asking_is_bounded(self):
        Site.files = {f'/cdn/{n}': ('image/png', 100) for n in range(80)}
        Site.page = ''.join(f'<img src="/cdn/{n}">' for n in range(80))
        found = pagescrape.scrape(f'{self.base}/page')
        self.assertEqual(len(found.items), pagescrape.ASK_MOST)


if __name__ == '__main__':
    unittest.main()
