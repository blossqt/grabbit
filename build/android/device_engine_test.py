"""Runs Grabbit's engine on the phone with no screen involved.

The APK's Python side can be started by hand through ``run-as``, which means
the engine can be tested on the real device - real aria2, real yt-dlp, real
QuickJS, the phone's own network - without unlocking it or touching the UI.
build/android/device_test.ps1 pushes this in and runs it.

    [PASS]/[FAIL] lines, then a count, same as the other checks here.
"""

import os
import sys
import time

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, 'shared'))

from grabbit.settings import Settings            # noqa: E402
from grabbit.tasks import State                  # noqa: E402
from grabbit.util import human_size              # noqa: E402
from grabbit_mobile import paths                 # noqa: E402
from grabbit_mobile.bootstrap import unpack_tools  # noqa: E402
from grabbit_mobile.engine import MobileEngine   # noqa: E402

YOUTUBE = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
TIKTOK = 'https://www.tiktok.com/@jade.wood/video/7434103280294300960'
PHOTOS = 'https://www.instagram.com/nasa/p/DdWojaYFDf-/'

results = []


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''), flush=True)


def wait_for(engine, kind, seconds):
    """Wait for every task of one kind to finish, and return them."""
    deadline = time.time() + seconds
    while time.time() < deadline:
        tasks = [t for t in engine.store if t.kind == kind]
        if tasks and all(t.state in (State.COMPLETED, State.ERROR) for t in tasks):
            return tasks
        time.sleep(2)
    return [t for t in engine.store if t.kind == kind]


def can_resolve_names() -> bool:
    """Whether this process can use DNS at all.

    Started through run-as, it cannot: that sandbox is allowed sockets but not
    Android's resolver, so every hostname fails. The app itself has no such
    problem - but a test run this way has to say so rather than report a string
    of failures that mean nothing.
    """
    import socket
    try:
        socket.getaddrinfo('youtube.com', 443)
        return True
    except OSError:
        return False


def main():
    tools = unpack_tools()
    report('the bundled tools are where the app can run them', len(tools) == 4,
           ', '.join(sorted(tools)))
    print(f'  downloads go to {paths.downloads_dir()}')

    settings = Settings.load()
    settings.download_dir = str(paths.downloads_dir())
    settings.embed_thumbnail = settings.embed_metadata = False
    settings.filename_template = '%(title).40B.%(ext)s'

    messages = []
    engine = MobileEngine(settings, on_message=lambda level, text: messages.append((level, text)))
    started = engine.start()
    report('aria2 starts on the phone', started,
           f'version {engine.process.version}' if engine.process else 'no process')
    if not started:
        return 1

    if not can_resolve_names():
        engine.shutdown()
        print('\nDNS is not available to a run-as process, so the downloads cannot be\n'
              'tested this way. Everything up to the network has been checked; for the\n'
              'rest, open the app on the phone and share a link to it.')
        return 0

    try:
        for label, url, kind, seconds in (
                ('YouTube', YOUTUBE, 'media', 300),
                ('TikTok', TIKTOK, 'media', 300),
                ('a photo post', PHOTOS, 'image', 240)):
            print(f'\n-- {label} --', flush=True)
            before = {t.id for t in engine.store}
            engine.add_link(url)
            tasks = [t for t in wait_for(engine, kind, seconds) if t.id not in before]
            done = [t for t in tasks if t.state == State.COMPLETED
                    and os.path.exists(t.file_path or '')]
            report(f'{label}: downloads', bool(done) and len(done) == len(tasks),
                   ', '.join(f'{os.path.basename(t.file_path)} {human_size(t.total)}'
                             for t in done)
                   or (tasks[0].error if tasks else 'nothing was added'))

        print('\n-- the same video as a GIF --', flush=True)
        before = {t.id for t in engine.store}
        engine.add_link(YOUTUBE, 'gif')
        tasks = [t for t in wait_for(engine, 'media', 420) if t.id not in before]
        gif = next((t for t in tasks if (t.file_path or '').endswith('.gif')), None)
        report('FFmpeg makes a GIF on the phone', bool(gif) and os.path.exists(gif.file_path),
               f'{os.path.basename(gif.file_path)} {human_size(os.path.getsize(gif.file_path))}'
               if gif and os.path.exists(gif.file_path)
               else (tasks[0].error if tasks else 'no task'))
    finally:
        engine.shutdown()

    print('\n-- what the engine said --')
    for level, text in messages[-8:]:
        print(f'   [{level}] {text[:120]}')

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
