"""A torrent whose file is already where it is to be saved, without aria2's control file (ondisk.py):
its own pieces are kept and only the rest fetched; another file of the same name is left alone.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe tests\\test_ondisk.py

The aria2 tests run the real aria2c that ships with Grabbit, with no peers to reach: the file there
is checked, nothing is downloaded.
"""

import hashlib
import os
import random
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))

from PySide6.QtWidgets import QApplication  # noqa: E402

from grabbit import ondisk, paths  # noqa: E402

APP = QApplication.instance() or QApplication([])
PIECE = 64 * 1024
ARIA2C = Path(__file__).resolve().parents[1] / 'dist' / 'Grabbit' / 'tools' / 'aria2c.exe'


def bencode(value) -> bytes:
    if isinstance(value, int):
        return b'i%de' % value
    if isinstance(value, str):
        value = value.encode()
    if isinstance(value, bytes):
        return b'%d:%s' % (len(value), value)
    if isinstance(value, list):
        return b'l' + b''.join(bencode(v) for v in value) + b'e'
    return b'd' + b''.join(bencode(k) + bencode(value[k]) for k in sorted(value)) + b'e'


def torrent(name: str, contents: dict | bytes) -> bytes:
    """A .torrent for one file (bytes) or several ({path: bytes}), PIECE-sized pieces."""
    joined = contents if isinstance(contents, bytes) else b''.join(contents.values())
    pieces = b''.join(hashlib.sha1(joined[i:i + PIECE]).digest() for i in range(0, len(joined), PIECE))
    info = {'name': name, 'piece length': PIECE, 'pieces': pieces}
    if isinstance(contents, bytes):
        info['length'] = len(contents)
    else:
        info['files'] = [{'path': path.split('/'), 'length': len(data)} for path, data in contents.items()]
    return bencode({'info': info})


def noise(size: int, seed: int) -> bytes:
    return random.Random(seed).randbytes(size)


class LookTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.data = noise(40 * PIECE + 1234, 1)
        self.meta = torrent('movie.mkv', self.data)
        self.path = os.path.join(self.dir, 'movie.mkv')

    def put(self, data: bytes, path=None):
        Path(path or self.path).write_bytes(data)

    def look(self, data=None, select=''):
        return ondisk.look(self.dir, data or self.meta, select)

    def test_nothing_there(self):
        self.assertEqual(self.look(), ondisk.NOTHING)
        self.assertFalse(ondisk.needs_look(self.dir, self.meta))

    def test_its_control_file_there_is_left_to_aria2(self):
        self.put(self.data[:5 * PIECE])
        self.put(b'', self.path + '.aria2')
        self.assertEqual(self.look(), ondisk.RESUME)
        self.assertFalse(ondisk.needs_look(self.dir, self.meta))

    def test_made_full_size_and_cut_off_is_checked(self):
        """The case that was seen: written up to a point, zeros from there to its full size."""
        cut = 31 * PIECE
        self.put(self.data[:cut] + bytes(len(self.data) - cut))
        self.assertTrue(ondisk.needs_look(self.dir, self.meta))
        self.assertEqual(self.look(), ondisk.CHECK)

    def test_cut_short_is_checked(self):
        self.put(self.data[:9 * PIECE + 77])
        self.assertEqual(self.look(), ondisk.CHECK)

    def test_full_size_with_nothing_written_is_checked(self):
        self.put(bytes(len(self.data)))
        self.assertEqual(self.look(), ondisk.CHECK)

    def test_empty_file_is_checked(self):
        self.put(b'')
        self.assertEqual(self.look(), ondisk.CHECK)

    def test_a_torrents_scattered_pieces_are_checked(self):
        """A torrent fills pieces in any order: a few here and there, zeros between."""
        block = bytearray(len(self.data))
        for index in (3, 17, 29):
            block[index * PIECE:(index + 1) * PIECE] = self.data[index * PIECE:(index + 1) * PIECE]
        self.put(bytes(block))
        self.assertEqual(self.look(), ondisk.CHECK)

    def test_another_file_of_the_same_name_is_left_alone(self):
        self.put(noise(len(self.data), 2))
        self.assertEqual(self.look(), ondisk.OTHER)

    def test_a_small_other_file_is_left_alone(self):
        self.put(b'my own notes, nothing to do with the torrent')
        self.assertEqual(self.look(), ondisk.OTHER)

    def test_several_files(self):
        parts = {'a.bin': noise(5 * PIECE + 10, 3), 'sub/b.bin': noise(7 * PIECE + 99, 4), 'c.txt': noise(500, 5)}
        meta = torrent('pack', parts)
        folder = os.path.join(self.dir, 'pack')
        os.makedirs(os.path.join(folder, 'sub'))
        self.put(parts['sub/b.bin'], os.path.join(folder, 'sub', 'b.bin'))
        self.assertTrue(ondisk.needs_look(self.dir, meta))
        self.assertEqual(self.look(meta), ondisk.CHECK)
        self.put(noise(7 * PIECE + 99, 6), os.path.join(folder, 'sub', 'b.bin'))
        self.assertEqual(self.look(meta), ondisk.OTHER)
        # Only the files chosen count: one not chosen is aria2's to leave alone.
        self.assertEqual(self.look(meta, select='1,3'), ondisk.NOTHING)

    def test_reads_are_bounded(self):
        self.put(self.data)
        opened = []
        real = open

        def counting(*args, **kwargs):
            opened.append(args[0])
            return real(*args, **kwargs)

        import builtins
        builtins.open = counting
        try:
            self.assertEqual(self.look(), ondisk.CHECK)
        finally:
            builtins.open = real
        self.assertLessEqual(len(opened), ondisk.SAMPLES)

    def test_select_spec(self):
        self.assertEqual(ondisk.chosen('1,3-5'), {1, 3, 4, 5})
        self.assertEqual(ondisk.chosen(''), set())


@unittest.skipUnless(ARIA2C.is_file(), 'aria2c.exe is built into dist\\Grabbit\\tools')
class Aria2Tests(unittest.TestCase):
    """What aria2 itself does with such a file, with and without check-integrity."""

    @classmethod
    def setUpClass(cls):
        from grabbit.aria2rpc import Aria2Process
        cls.dir = tempfile.mkdtemp()
        cls.process = Aria2Process(str(ARIA2C), {
            'dir': cls.dir, 'enable-dht': 'false', 'enable-dht6': 'false', 'bt-enable-lpd': 'false',
            'enable-peer-exchange': 'false', 'file-allocation': 'none', 'listen-port': '6990-6999',
            'seed-time': '0', 'allow-overwrite': 'false', 'auto-file-renaming': 'true', 'continue': 'true'})
        cls.client = cls.process.start()

    @classmethod
    def tearDownClass(cls):
        cls.process.stop()
        shutil.rmtree(cls.dir, True)

    def add(self, name: str, data: bytes, written: int, **options):
        folder = os.path.join(self.dir, name)
        os.makedirs(folder)
        path = os.path.join(folder, 'movie.mkv')
        Path(path).write_bytes(data[:written] + bytes(len(data) - written))
        import base64
        gid = self.client.call('aria2.addTorrent', base64.b64encode(torrent('movie.mkv', data)).decode(), [],
                               {'dir': folder, 'continue': 'true', **options})
        status = {}
        for _ in range(100):
            status = self.client.call('aria2.tellStatus', gid)
            if status['status'] == 'error' or (int(status.get('completedLength') or 0)
                                               and not status.get('verifiedLength')):
                break
            time.sleep(0.05)
        self.client.call('aria2.forceRemove', gid) if status['status'] != 'error' else None
        time.sleep(0.2)
        return status, path

    def test_without_a_check_aria2_refuses(self):
        data = noise(20 * PIECE, 7)
        status, _ = self.add('plain', data, 12 * PIECE)
        self.assertEqual(status['status'], 'error')
        self.assertEqual(status['errorCode'], '13')       # the file already existed

    def test_with_a_check_it_keeps_what_is_there(self):
        data = noise(20 * PIECE, 8)
        status, path = self.add('checked', data, 12 * PIECE, **{'check-integrity': 'true'})
        self.assertNotEqual(status['status'], 'error', status.get('errorMessage'))
        self.assertEqual(int(status['completedLength']), 12 * PIECE)
        self.assertEqual(Path(path).read_bytes()[:12 * PIECE], data[:12 * PIECE])


class FakeClient:
    def __init__(self):
        self.added = []

    def call(self, method, *args):
        if method == 'aria2.addTorrent':
            self.added.append(args[2])
            return args[2]['gid']
        return None


class EngineTests(unittest.TestCase):
    """The engine looks first and hands the torrent over as the look says, off the window's thread."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)
        paths.set_data_dir(os.path.join(self.root, 'data'))
        from grabbit.engine import Engine
        from grabbit.settings import Settings
        self.saves = os.path.join(self.root, 'saves')
        os.makedirs(self.saves)
        self.engine = Engine(Settings(download_dir=self.saves))
        self.engine.client = FakeClient()
        self.engine.running = True
        self.data = noise(30 * PIECE, 9)

    def settle(self, task):
        end = time.perf_counter() + 5
        while time.perf_counter() < end and task.id in self.engine._looks:
            APP.processEvents()
            time.sleep(0.005)
        APP.processEvents()

    def test_nothing_there_goes_straight_to_aria2(self):
        task = self.engine.add_torrent(torrent('movie.mkv', self.data))
        self.assertEqual(len(self.engine.client.added), 1)
        self.assertNotIn('check-integrity', self.engine.client.added[0])
        self.assertTrue(task.gid)

    def test_its_own_file_cut_off_is_checked_and_filled_in(self):
        Path(self.saves, 'movie.mkv').write_bytes(self.data[:20 * PIECE] + bytes(10 * PIECE))
        task = self.engine.add_torrent(torrent('movie.mkv', self.data))
        self.assertEqual(task.state, 'checking')
        self.settle(task)
        self.assertEqual(self.engine.client.added[0].get('check-integrity'), 'true')
        self.assertEqual(task.state, 'queued')
        self.assertFalse(task.error)

    def test_another_file_is_left_alone_and_said_so(self):
        Path(self.saves, 'movie.mkv').write_bytes(noise(30 * PIECE, 10))
        task = self.engine.add_torrent(torrent('movie.mkv', self.data))
        self.settle(task)
        self.assertEqual(self.engine.client.added, [])
        self.assertEqual(task.state, 'error')
        self.assertIn('Another file named', task.error)

    def test_paused_while_looking_is_handed_over_paused(self):
        Path(self.saves, 'movie.mkv').write_bytes(self.data[:20 * PIECE] + bytes(10 * PIECE))
        task = self.engine.add_torrent(torrent('movie.mkv', self.data))
        self.engine.pause([task.id])
        self.settle(task)
        self.assertEqual(self.engine.client.added[0].get('pause'), 'true')
        self.assertEqual(task.state, 'paused')

    def test_removed_while_looking_is_never_handed_over(self):
        Path(self.saves, 'movie.mkv').write_bytes(self.data[:20 * PIECE] + bytes(10 * PIECE))
        task = self.engine.add_torrent(torrent('movie.mkv', self.data))
        self.engine.store.remove(task.id)
        end = time.perf_counter() + 1
        while time.perf_counter() < end:
            APP.processEvents()
        self.assertEqual(self.engine.client.added, [])


if __name__ == '__main__':
    unittest.main()
