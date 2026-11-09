"""A Gofile album (gallerydl.py and the add dialog's gallery card): a mix of files, each sent with
the visitor's token, and folders inside it followed.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe tests\\test_gofile.py

gallery-dl stands in here, printing what it prints for a Gofile album. Gofile's own accounts
address is a web server of the test's on 127.0.0.1.
"""

import json
import os
import subprocess
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from grabbit import analyze, gallerydl  # noqa: E402

APP = QApplication.instance() or QApplication([])

ALBUM = 'https://gofile.io/d/AbCd12'


def file(name, extension, size, **more):
    return {'category': 'gofile', 'subcategory': 'folder', 'type': 'file', 'filename': name, 'extension': extension,
            'size': size, 'id': name, **more}


LISTING = [
    [2, {'category': 'gofile', 'name': 'Holiday', 'code': 'AbCd12'}],
    [3, 'https://store-eu-par-2.gofile.io/download/web/1/clip.mkv', file('clip', 'mkv', 9_000_000)],
    [3, 'https://store-eu-par-2.gofile.io/download/web/2/photo.jpg',
     file('photo', 'jpg', 2_000, thumbnail='https://store-eu-par-2.gofile.io/download/web/2/thumb.jpg')],
    [3, 'https://store-eu-par-2.gofile.io/download/web/3/notes.zip', file('notes', 'zip', 500)],
    [6, 'https://gofile.io/d/inner', {'type': 'folder'}],
    [2, {'category': 'gofile', 'name': 'Inside'}],
    [3, 'https://store-eu-par-2.gofile.io/download/web/4/song.mp3', file('song', 'mp3', 4_000)],
]
NOT_THERE = [[-1, {'error': 'NotFoundError', 'message': 'Requested content could not be found'}]]


def printed(messages, code=0):
    return subprocess.CompletedProcess([], code, stdout=json.dumps(messages), stderr='')


class ProbeTests(unittest.TestCase):
    def probe(self, url, messages, token='tok'):
        with mock.patch.object(gallerydl, 'command', lambda: ['gallery-dl']), \
                mock.patch.object(gallerydl, '_gofile_visitor', mock.Mock(return_value=token)) as self.visitor, \
                mock.patch.object(gallerydl.subprocess, 'run', mock.Mock(return_value=printed(messages))) as self.run:
            return gallerydl.probe(url, SimpleNamespace())

    @property
    def args(self):
        return self.run.call_args[0][0]

    def test_every_kind_of_file(self):
        found = self.probe(ALBUM, LISTING)
        self.assertEqual([(i.filename, i.group) for i in found.items],
                         [('clip.mkv', 'video'), ('photo.jpg', 'image'), ('notes.zip', 'file'), ('song.mp3', 'audio')])
        self.assertTrue(all(i.kind == 'image' and i.direct_url for i in found.items), 'each is a direct download')
        self.assertEqual(found.items[0].filesize, 9_000_000)
        self.assertEqual(found.items[1].thumbnail, 'https://store-eu-par-2.gofile.io/download/web/2/thumb.jpg')
        self.assertEqual(found.title, 'Holiday', 'the album, not a folder inside it')

    def test_each_file_goes_with_the_token(self):
        found = self.probe(ALBUM, LISTING)
        self.assertIn('api-token=tok', self.args, 'gallery-dl reads the album as the same visitor')
        for item in found.items:
            self.assertEqual(item.headers, {'Referer': 'https://gofile.io/', 'Cookie': 'accountToken=tok'})

    def test_folders_inside_are_followed(self):
        self.probe(ALBUM, LISTING)
        self.assertIn('--resolve-json', self.args)

    def test_no_token_to_be_had(self):
        found = self.probe(ALBUM, LISTING, token='')
        self.assertNotIn('Cookie', found.items[0].headers)
        self.assertFalse([a for a in self.args if a.startswith('api-token')])

    def test_the_sites_own_words(self):
        found = self.probe(ALBUM, NOT_THERE)
        self.assertEqual(found.error, 'Gofile: Requested content could not be found')
        with mock.patch.object(analyze.gallerydl, 'available', lambda: True), \
                mock.patch.object(analyze.gallerydl, 'probe', lambda url, settings: found), \
                mock.patch.object(analyze, 'matching_extractor', lambda url: ''):
            shown = analyze.analyze(ALBUM, None)
        self.assertEqual(shown.kind, analyze.KIND_ERROR)
        self.assertIn('could not be found', shown.error)

    def test_other_sites_are_as_they_were(self):
        found = self.probe('https://www.example.com/album/1', [
            [2, {'category': 'example', 'title': 'Trip'}],
            [3, 'https://cdn.example.com/a.jpg', {'category': 'example', 'filename': 'a', 'extension': 'jpg', 'size': 7}]])
        self.visitor.assert_not_called()
        self.assertIn('--dump-json', self.args)
        self.assertEqual(found.items[0].headers, {'Referer': 'https://www.example.com/'})
        self.assertIsNone(found.items[0].filesize, '"size" is only Gofile\'s file size')

    def test_someone_elses_address_that_looks_like_it(self):
        self.assertFalse(gallerydl._is_gofile('https://gofile.io.example.com/d/AbCd12'))
        self.assertTrue(gallerydl._is_gofile('https://www.gofile.io/d/AbCd12'))


class Accounts(BaseHTTPRequestHandler):
    seen: list = []
    answer = b'{"status": "ok", "data": {"token": "visitor"}}'

    def do_POST(self):
        Accounts.seen.append((self.path, self.headers.get('Origin'), self.headers.get('Cookie')))
        self.send_response(200)
        self.send_header('Content-Length', str(len(self.answer)))
        self.end_headers()
        self.wfile.write(self.answer)

    def log_message(self, *args):
        pass


class VisitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Accounts)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.address = f'http://127.0.0.1:{cls.server.server_address[1]}/accounts'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_asks_as_the_site_does(self):
        Accounts.seen.clear()
        with mock.patch.object(gallerydl, 'GOFILE_ACCOUNTS', self.address):
            self.assertEqual(gallerydl._gofile_visitor(), 'visitor')
        self.assertEqual(Accounts.seen, [('/accounts', 'https://gofile.io', None)])

    def test_a_site_that_says_no(self):
        with mock.patch.object(gallerydl, 'GOFILE_ACCOUNTS', self.address + 'x'), \
                mock.patch.object(Accounts, 'answer', b'not json'):
            self.assertEqual(gallerydl._gofile_visitor(), '')


class CardTests(unittest.TestCase):
    def card(self):
        from grabbit.ui import add_dialog
        with mock.patch.object(gallerydl, 'command', lambda: ['gallery-dl']), \
                mock.patch.object(gallerydl, '_gofile_visitor', lambda: 'tok'), \
                mock.patch.object(gallerydl.subprocess, 'run', lambda *a, **k: printed(LISTING)):
            found = gallerydl.probe(ALBUM, SimpleNamespace())
        analysis = analyze.Analysis(url=ALBUM, kind=analyze.KIND_GALLERY, probe=found)
        return add_dialog.GalleryCard(analysis, SimpleNamespace(video_quality='best', video_container='mp4'))

    def test_says_what_is_in_it(self):
        self.assertIn('1 photo, 1 video, 1 audio track, 1 file', self.card().subtitle.text())

    def test_every_file_is_fetched_directly(self):
        requests = self.card().requests()
        self.assertEqual([r['type'] for r in requests], ['image'] * 4, 'not handed to the video downloader')


if __name__ == '__main__':
    unittest.main(verbosity=2)
