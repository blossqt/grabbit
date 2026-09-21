r"""Checks that a frame can be chosen and saved from a video that is not downloaded.

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\frames_check.py

FFmpeg makes a short video whose brightness climbs steadily - so the brightness
of any frame says when in the video it came from - and a small web server that
answers range requests, as real video hosts do, serves it. Then the same code
both apps use reads frames out of it over HTTP, and the checks look at where
each one came from, how much of the video it cost, and what was written.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from grabbit import frames, media               # noqa: E402
from grabbit.paths import find_tool             # noqa: E402
from grabbit.settings import Settings           # noqa: E402

results = []
SECONDS = 20
# Brightness rises by this much a second, from 16, so frame luma says the time.
RATE = 10


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''), flush=True)


class RangeHandler(SimpleHTTPRequestHandler):
    """Serves byte ranges, and counts what it sent."""
    sent = 0

    def log_message(self, *args):
        pass

    def do_GET(self):
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return
        size = path.stat().st_size
        start, end = 0, size - 1
        header = self.headers.get('Range', '')
        if header.startswith('bytes='):
            first, _, last = header[6:].split(',')[0].partition('-')
            start = int(first) if first else max(0, size - int(last))
            end = min(size - 1, int(last)) if first and last else size - 1
            self.send_response(206)
            self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
        else:
            self.send_response(200)
        self.send_header('Content-Type', 'video/mp4')
        self.send_header('Accept-Ranges', 'bytes')
        self.send_header('Content-Length', str(end - start + 1))
        self.end_headers()
        with path.open('rb') as handle:
            handle.seek(start)
            remaining = end - start + 1
            try:
                while remaining:
                    chunk = handle.read(min(1 << 16, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    RangeHandler.sent += len(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass


def make_video(folder: Path) -> Path:
    """Brightness climbing RATE a second, with noise so it is a realistic size."""
    target = folder / 'climb.mp4'
    subprocess.run([
        find_tool('ffmpeg'), '-v', 'error', '-y', '-f', 'lavfi',
        '-i', f'color=c=black:s=640x360:r=30:d={SECONDS}',
        '-vf', f"geq=lum='16+{RATE}*T':cb=128:cr=128,noise=alls=12:allf=t",
        '-c:v', 'libx264', '-preset', 'veryfast', '-g', '30', '-pix_fmt', 'yuv420p',
        '-movflags', '+faststart', str(target)], check=True)
    return target


def luma_of_jpeg(data: bytes) -> float:
    """Mean brightness of an image, on the video's own scale."""
    result = subprocess.run([find_tool('ffmpeg'), '-v', 'error', '-i', 'pipe:0', '-vf', 'scale=32:18',
                             '-f', 'rawvideo', '-pix_fmt', 'gray', 'pipe:1'],
                            input=data, capture_output=True, check=True)
    pixels = result.stdout
    return sum(pixels) / len(pixels)


def when(data: bytes) -> float:
    # JPEG and full-range grey both shift the scale a little; this undoes the
    # limited-to-full range stretch closely enough for a tenth of a second.
    full = luma_of_jpeg(data)
    limited = 16 + full * 219 / 255
    return (limited - 16) / RATE


def main():
    if not find_tool('ffmpeg'):
        print('FFmpeg is missing - run build\\bootstrap.ps1 first')
        return 1
    scratch = Path(tempfile.mkdtemp(prefix='grabbit-frames-'))
    server = None
    try:
        video = make_video(scratch)
        size = video.stat().st_size
        server = ThreadingHTTPServer(('127.0.0.1', 0), lambda *a: RangeHandler(*a, directory=str(scratch)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        url = f'http://127.0.0.1:{server.server_port}/climb.mp4'
        settings = Settings()
        print(f'\nreading frames from a {size / 1e6:.1f} MB video over HTTP')

        stream = frames.find_stream(url, settings)
        report('the stream is found, with its length', abs(stream.duration - SECONDS) < 0.5,
               f'{stream.duration:.1f}s, {stream.width}x{stream.height}')

        RangeHandler.sent = 0
        started = time.monotonic()
        data = frames.preview(stream, 15.0)
        took = time.monotonic() - started
        report('a frame from late on comes from the right moment', abs(when(data) - 15.0) < 0.35,
               f'asked for 15.0s, it shows {when(data):.2f}s')
        report('without reading the whole video', RangeHandler.sent < size * 0.35,
               f'{RangeHandler.sent / 1e6:.2f} of {size / 1e6:.1f} MB, {took:.1f}s')
        early = frames.preview(stream, 2.5)
        report('and one from early on, from its moment', abs(when(early) - 2.5) < 0.35,
               f'{when(early):.2f}s')
        report('a preview is a JPEG no wider than asked', early[:3] == b'\xff\xd8\xff')

        print('\nsaving a frame')
        png = frames.save(stream.url, stream.headers, 7.0, str(scratch / 'still.png'))
        jpg = frames.save(stream.url, stream.headers, 7.0, str(scratch / 'still.jpg'))
        report('as PNG', Path(png).read_bytes()[:8] == b'\x89PNG\r\n\x1a\n')
        report('as JPG', Path(jpg).read_bytes()[:3] == b'\xff\xd8\xff')
        report('at the moment chosen', abs(when(Path(jpg).read_bytes()) - 7.0) < 0.35,
               f'{when(Path(jpg).read_bytes()):.2f}s')
        report('with nothing half-written left beside it',
               not list(scratch.glob('*.part')))
        report('the name says when it is from', frames.frame_filename('A: title', 83.45, 'png')
               == 'A_ title @ 1m23.4s.png', frames.frame_filename('A: title', 83.45, 'png'))
        try:
            frames.save(url.replace('climb', 'missing'), {}, 1.0, str(scratch / 'none.png'))
            report('a stream that is not there is an error', False)
        except frames.FrameError as exc:
            report('a stream that is not there is an error', True, str(exc)[:60])

        print('\nmoving the slider')
        seen, errors, done = [], [], threading.Event()

        def on_frame(at, data):
            seen.append(at)
            if at == 12.0:
                done.set()

        reader = frames.FrameReader(url, settings, on_frame, errors.append)
        for at in (1.0, 3.0, 5.0, 8.0, 12.0):     # a quick drag, then a stop
            reader.want(at)
            time.sleep(0.02)
        done.wait(60)
        report('the frame where it stopped is shown', 12.0 in seen and not errors,
               f'read {seen}')
        report('and the ones it passed over are skipped', len(seen) <= 3, f'{len(seen)} read')
        reader.close()

        print('\nthe download, as either app queues it')
        events = []

        class Task:
            id, source, save_dir = 'frame-task', url, str(scratch / 'out')
            media = {'quality': 'frame', 'frame_at': 4.0, 'frame_format': 'jpg'}

        job = media.MediaJob(Task, settings, {
            'emit': lambda task_id, event, payload: events.append((event, payload)),
            'aria2_add': lambda *a: (_ for _ in ()).throw(RuntimeError('no aria2 here')),
            'aria2_status': lambda gid: {}, 'aria2_control': lambda gid, action: None})
        job.use_aria2 = False
        job.run()
        finished = [p for e, p in events if e == 'finished']
        saved = finished[0]['filepath'] if finished else ''
        report('it saves the frame, not the video', bool(saved) and saved.endswith('.jpg')
               and not list((scratch / 'out').glob('*.mp4')),
               os.path.basename(saved) or str([e for e in events if e[0] == 'error']))
        if saved:
            report('from the moment chosen', abs(when(Path(saved).read_bytes()) - 4.0) < 0.35,
                   f'{when(Path(saved).read_bytes()):.2f}s')
    finally:
        if server:
            server.shutdown()
        shutil.rmtree(scratch, ignore_errors=True)

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    if '--make-video' in sys.argv:
        # device_test.ps1 wants the video alone, to read frames from on a phone.
        folder = Path(sys.argv[sys.argv.index('--make-video') + 1])
        folder.mkdir(parents=True, exist_ok=True)
        print(make_video(folder))
        sys.exit(0)
    sys.exit(main())
