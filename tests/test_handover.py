r"""A second start of Grabbit handing its links to the one running - offscreen.

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe tests\test_handover.py

The server is Grabbit's own (app.SingleInstanceServer) under a name of the test's own, with a
window that only writes down what it is handed. Each sender is a process of its own, as a second
start is.
"""

import os
import subprocess
import sys
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
APP = Path(__file__).resolve().parents[1] / 'app'
sys.path.insert(0, str(APP))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

qt = QApplication.instance() or QApplication([])

from grabbit import app as grabbit  # noqa: E402

NAME = 'grabbit-handover-test'
grabbit.SERVER_NAME = NAME
SENDER = r'''
import sys, time
sys.path.insert(0, sys.argv[1])
from PySide6.QtCore import QCoreApplication
QCoreApplication([])
from grabbit import app as grabbit
grabbit.SERVER_NAME = sys.argv[2]
links = open(sys.argv[3], encoding='utf-8').read().splitlines()
if sys.argv[4] == 'own':
    sys.exit(0 if grabbit.send_to_running_instance(links) else 1)
with open('\\\\.\\pipe\\' + sys.argv[2], 'wb', buffering=0) as pipe:      # one that takes its time
    time.sleep(float(sys.argv[4]))
    pipe.write('\n'.join(links).encode('utf-8'))
'''


class _Window:
    def __init__(self):
        self.handed: list[list[str]] = []

    def handle_links(self, links):
        self.handed.append(list(links))


class HandoverTests(unittest.TestCase):
    server = window = None

    @classmethod
    def setUpClass(cls):
        cls.window = _Window()
        cls.server = grabbit.SingleInstanceServer(cls.window)      # one, as Grabbit has

    def setUp(self):
        self.window.handed.clear()

    def _send(self, links, how='own'):
        listed = Path(os.environ.get('TEMP', '.')) / f'grabbit-handover-{os.getpid()}.txt'
        listed.write_text('\n'.join(links), encoding='utf-8')
        return subprocess.Popen([sys.executable, '-c', SENDER, str(APP), NAME, str(listed), how])

    def _spin(self, until, seconds: float = 10) -> None:
        deadline = time.monotonic() + seconds
        while not until() and time.monotonic() < deadline:
            qt.processEvents()
            time.sleep(0.005)

    def test_a_long_list_arrives_whole(self):
        # However many pieces the pipe gives it in, all of it, once.
        links = [f'https://example.com/video/{n}?list=hunt&index={n}' for n in range(2000)]
        sender = self._send(links)
        self._spin(lambda: self.window.handed)
        self.assertEqual(sender.wait(10), 0)
        self.assertEqual(self.window.handed, [links])

    def test_one_that_takes_its_time_holds_nothing_up(self):
        # Waited for, it froze Grabbit's window for up to half a second.
        sender = self._send(['https://example.com/a'], '0.8')
        ticks = []
        timer = QTimer(interval=50)
        timer.timeout.connect(lambda: ticks.append(time.monotonic()))
        timer.start()
        self._spin(lambda: self.window.handed)
        timer.stop()
        sender.wait(10)
        gaps = [b - a for a, b in zip(ticks, ticks[1:])]
        self.assertEqual(self.window.handed, [['https://example.com/a']])
        self.assertLess(max(gaps), 0.15, f'never held up: {gaps}')

    def test_handed_over_again_and_again(self):
        for n in range(5):
            self.window.handed.clear()
            sender = self._send([f'magnet:?xt=urn:btih:{n}', f'https://example.com/{n}'])
            self._spin(lambda: self.window.handed)
            sender.wait(10)
            self.assertEqual(self.window.handed, [[f'magnet:?xt=urn:btih:{n}', f'https://example.com/{n}']])
        self._spin(lambda: False, 6)            # past the hand-over's own time limit: still here


if __name__ == '__main__':
    unittest.main(verbosity=2)
