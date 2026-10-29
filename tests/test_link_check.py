"""What Grabbit makes of a pasted link to a picture site (analyze.py, suiteweb.py): a page behind a
robot check, and a picture shown smaller than the site keeps it.

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe tests\test_link_check.py

The links go to a web server of the test's own on 127.0.0.1.
"""

import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from grabbit import analyze, suiteweb  # noqa: E402

JPEG = b'\xff\xd8\xff\xe0' + b'\0' * 2000


class Site(BaseHTTPRequestHandler):
    def _answer(self, body: bool):
        if self.path == '/i/blossom':                 # as Cloudflare answers a program
            page = b'<html><title>Just a moment...</title></html>'
            self.send_response(403)
            self.send_header('Content-Type', 'text/html; charset=UTF-8')
            self.send_header('Server', 'cloudflare')
            self.send_header('cf-mitigated', 'challenge')
            self.send_header('Content-Length', str(len(page)))
            self.end_headers()
            if body:
                self.wfile.write(page)
            return
        found = self.path in ('/public/a/blossom.jpg', '/public/a_large/blossom.jpg')
        self.send_response(200 if found else 404)
        self.send_header('Content-Type', 'image/jpeg' if found else 'text/html')
        self.send_header('Content-Length', str(len(JPEG) if found else 0))
        self.end_headers()
        if body and found:
            self.wfile.write(JPEG)

    def do_GET(self):
        self._answer(True)

    def do_HEAD(self):
        self._answer(False)

    def log_message(self, *args):
        pass


class LinkCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Site)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f'http://127.0.0.1:{cls.server.server_address[1]}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def test_a_page_behind_a_robot_check(self):
        with mock.patch.object(analyze, '_try_gallery_dl', lambda url, settings: None):
            found = analyze.analyze(f'{self.base}/i/blossom', None)
        self.assertEqual(found.kind, analyze.KIND_ERROR)
        self.assertTrue(found.robot, 'no "Download the file anyway": it would save the check')
        self.assertIn('checks for robots', found.error)
        self.assertIn('Copy image address', found.error)

    def test_the_full_size_picture(self):
        small, full = f'{self.base}/public/a_large/blossom.jpg', f'{self.base}/public/a/blossom.jpg'
        with mock.patch.object(suiteweb, 'best', lambda url, referer='': full if url == small else url):
            found = analyze.analyze(small, None)
        self.assertEqual((found.kind, found.url, found.filename), (analyze.KIND_FILE, full, 'blossom.jpg'))
        self.assertEqual(found.filesize, len(JPEG))

if __name__ == '__main__':
    unittest.main(verbosity=2)
