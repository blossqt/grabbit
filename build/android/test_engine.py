"""Exercises the phone engine off-device.

The Android engine is plain Python - no Qt, no Android APIs on the hot path -
so it can be run on a desktop against the desktop binaries. That catches
wiring mistakes long before an APK exists, which is the slowest way to find
out anything.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\android\\test_engine.py
"""

import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, '..', '..'))
sys.path.insert(0, os.path.join(ROOT, 'app'))
sys.path.insert(0, os.path.join(ROOT, 'android'))

WORKSPACE = tempfile.mkdtemp(prefix='grabbit-mobile-')
os.environ['ANDROID_PRIVATE'] = WORKSPACE

from grabbit import paths as shared_paths       # noqa: E402
from grabbit.settings import Settings           # noqa: E402
from grabbit.tasks import State                 # noqa: E402
from grabbit.util import human_size             # noqa: E402
from grabbit_mobile import paths as mobile_paths  # noqa: E402
from grabbit_mobile.engine import MobileEngine  # noqa: E402

YOUTUBE = 'https://www.youtube.com/watch?v=jNQXAC9IVRw'
PHOTO_POST = 'https://www.instagram.com/nasa/p/DdWojaYFDf-/'

results = []


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''), flush=True)


def main():
    print(f'workspace: {WORKSPACE}')

    # Stand in for the tools an APK would ship as native libraries.
    for tool in ('aria2c', 'ffmpeg'):
        found = shared_paths.find_tool(tool)
        if not found:
            report(f'{tool} available for the test', False, 'build the desktop app first')
            return 1
        mobile_paths.TOOL_PATHS[tool] = found
    report('desktop binaries stand in for the phone ones', True)

    settings = Settings()
    settings.download_dir = str(mobile_paths.downloads_dir())
    settings.embed_thumbnail = settings.embed_metadata = False
    settings.filename_template = '%(title).50B.%(ext)s'

    messages = []
    engine = MobileEngine(settings, on_message=lambda level, text: messages.append((level, text)))
    report('engine starts', engine.start(), f'aria2 {engine.process.version if engine.process else "?"}')
    if not engine.running:
        return 1

    try:
        print('\n-- a video --')
        engine.add_link(YOUTUBE)
        deadline = time.time() + 240
        while time.time() < deadline:
            tasks = [t for t in engine.store if t.kind == 'media']
            if tasks and tasks[0].state in (State.COMPLETED, State.ERROR):
                break
            if tasks:
                print(f'   {tasks[0].status_text}: {human_size(tasks[0].done)}    ', end='\r')
            time.sleep(1)
        print()
        video = next((t for t in engine.store if t.kind == 'media'), None)
        ok = bool(video) and video.state == State.COMPLETED and os.path.exists(video.file_path or '')
        report('downloads a video end to end', ok,
               f'{os.path.basename(video.file_path)} {human_size(video.total)}' if ok
               else (video.error if video else 'no task was created'))

        print('\n-- a photo post --')
        before = len(list(engine.store))
        engine.add_link(PHOTO_POST)
        deadline = time.time() + 180
        while time.time() < deadline:
            photos = [t for t in engine.store if t.kind == 'image']
            if photos and all(t.state in (State.COMPLETED, State.ERROR) for t in photos):
                break
            time.sleep(1)
        photos = [t for t in engine.store if t.kind == 'image']
        done = [t for t in photos if t.state == State.COMPLETED]
        report('downloads every photo in a post', bool(done) and len(done) == len(photos),
               f'{len(done)} of {len(photos)} saved')
        report('the task list grew', len(list(engine.store)) > before)

        print('\n-- pausing and removing --')
        if photos:
            engine.remove([photos[0].id], delete_files=True)
            report('removing a download works', engine.store.get(photos[0].id) is None)

        saved = list(mobile_paths.downloads_dir().glob('*'))
        report('files landed in the downloads folder', bool(saved),
               ', '.join(p.name[:28] for p in saved[:3]))
    finally:
        engine.shutdown()

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    for level, text in messages[-6:]:
        print(f'  engine said [{level}]: {text[:100]}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
