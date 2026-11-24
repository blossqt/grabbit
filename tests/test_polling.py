"""aria2 is asked about every download each second only while something moves: with everything done,
paused or failed, every few seconds; and starting a download asks each second again at once.
    <Grabbit's venv python> tests\\test_polling.py"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtCore import QCoreApplication  # noqa: E402

app = QCoreApplication.instance() or QCoreApplication([])

from grabbit import paths  # noqa: E402


class PollingTests(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        paths.set_data_dir(os.path.join(self.root, 'data'))
        from grabbit import engine
        from grabbit.settings import Settings
        self.module = engine
        self.engine = engine.Engine(Settings(download_dir=os.path.join(self.root, 'saves')))

    def test_slow_while_nothing_moves_and_quick_once_something_starts(self):
        e, m = self.engine, self.module
        e._apply_snapshot({'statuses': [{'gid': 'a', 'status': 'complete'}, {'gid': 'b', 'status': 'paused'}],
                           'stats': {}})
        self.assertEqual(e._poll_timer.interval(), m.POLL_IDLE_MS)
        e._apply_snapshot({'statuses': [{'gid': 'a', 'status': 'active'}], 'stats': {}})
        self.assertEqual(e._poll_timer.interval(), m.POLL_MS, 'downloading or seeding: each second')
        e._apply_snapshot({'statuses': [], 'stats': {}})
        self.assertEqual(e._poll_timer.interval(), m.POLL_IDLE_MS)
        e.client = None                     # (no aria2 here: the start itself fails, as it may)
        try:
            e.add_direct('https://example.com/file.bin')
        except Exception:                   # noqa: BLE001 - only the polling matters here
            pass
        self.assertEqual(e._poll_timer.interval(), m.POLL_MS, 'asked each second again as a download starts')


if __name__ == '__main__':
    unittest.main()
