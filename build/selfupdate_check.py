r"""Checks that Grabbit can replace itself - and put itself back when it cannot.

    %LOCALAPPDATA%\GrabbitBuild\venv\Scripts\python.exe build\selfupdate_check.py
    ... --real      and then the whole thing with the real Grabbit.exe

The swap is a PowerShell script that renames folders while programs start and
stop around it: exactly the kind of thing that only goes wrong on a real disk.
So this runs it on one, with a small stand-in program compiled for the purpose
that can play a new version that comes up, one that dies on start, and an
aria2 left running in the old folder.

--real does it end to end with dist\Grabbit: a copy is "installed" in a scratch
folder, a local stand-in for GitHub serves a manifest signed with a throwaway
key, and that copy is told to update itself. It downloads the zip, checks it,
unpacks it, starts it with --self-test, quits, and is swapped for it.
"""

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'app'))

from grabbit import ed25519, selfupdate, updates  # noqa: E402

results = []

STUB = r'''
using System; using System.IO; using System.Threading;
public static class Stub {
    public static int Main(string[] args) {
        string here = AppDomain.CurrentDomain.BaseDirectory;
        File.AppendAllText(Path.Combine(here, "launches.txt"), string.Join(" ", args) + Environment.NewLine);
        string mode = "ok", modeFile = Path.Combine(here, "mode.txt");
        if (File.Exists(modeFile)) mode = File.ReadAllText(modeFile).Trim();
        if (mode == "die") return 3;
        for (int i = 0; i + 1 < args.Length; i++)
            if (args[i] == "--update-marker") File.WriteAllText(args[i + 1], "started");
        Thread.Sleep(mode == "linger" ? 60000 : 4000);
        return 0;
    }
}
'''


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''), flush=True)


def compile_stub(target: Path) -> None:
    source = target.with_suffix('.cs')
    source.write_text(STUB, encoding='utf-8')
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    f"Add-Type -TypeDefinition (Get-Content -Raw -LiteralPath '{source}') "
                    f"-OutputAssembly '{target}' -OutputType WindowsApplication"],
                   check=True, capture_output=True)


def program(folder: Path, stub: Path, mode: str, name='Grabbit.exe') -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(stub, folder / name)
    (folder / 'mode.txt').write_text(mode, encoding='ascii')
    return folder / name


# The stand-in writes these beside itself, and the checks mark each copy with
# one, so the swap has to count them as Grabbit's own here.
STUB_FILES = ('mode.txt', 'launches.txt', 'old.txt', 'new.txt')


def run_helper(scratch: Path, install: Path, staged: Path, version: str, old_pid: int):
    script = scratch / 'install-update.ps1'
    script.write_text(selfupdate.HELPER_SCRIPT, encoding='utf-8-sig')
    marker, log = scratch / f'started-{version}', scratch / 'install-update.log'
    log.unlink(missing_ok=True)
    result = subprocess.run(
        ['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File',
         str(script), '-OldPid', str(old_pid), '-Install', str(install), '-Staged', str(staged),
         '-Version', version, '-Marker', str(marker), '-Log', str(log),
         '-Ours', '|'.join(selfupdate.SHIPPED + selfupdate.HARMLESS + STUB_FILES)],
        cwd=str(scratch), capture_output=True, text=True, timeout=300)
    text = log.read_text(encoding='utf-8', errors='replace') if log.exists() else ''
    return result.returncode, marker, text


def wait_for_launch(install: Path) -> str:
    """The script starts a copy and leaves; give it a moment to record that it ran."""
    launches = install / 'launches.txt'
    for _ in range(30):
        if launches.exists():
            return launches.read_text()
        time.sleep(0.5)
    return ''


def old_copy() -> subprocess.Popen:
    """Something to stand for the running Grabbit: it quits a moment later."""
    return subprocess.Popen(['powershell', '-NoProfile', '-Command', 'Start-Sleep -Seconds 2'],
                            creationflags=selfupdate.CREATE_NO_WINDOW)


def check_swap(scratch: Path, stub: Path) -> None:
    print('\nthe swap, with stand-ins')
    base = scratch / 'swap'
    install, staged = base / 'Grabbit', base / 'Grabbit.update'
    program(install, stub, 'ok')
    (install / 'old.txt').write_text('old')
    (install / 'portable.txt').write_text('')
    (install / 'data').mkdir()
    (install / 'data' / 'tasks.json').write_text('{"tasks": []}')
    linger = program(install / 'tools', stub, 'linger', 'aria2c.exe')
    left_running = subprocess.Popen([str(linger)])
    program(staged, stub, 'ok')
    (staged / 'new.txt').write_text('new')

    started = time.monotonic()
    code, marker, log = run_helper(scratch, install, staged, '9.9.9', old_copy().pid)
    took = time.monotonic() - started
    report('the new version is swapped in', code == 0 and (install / 'new.txt').exists()
           and not (install / 'old.txt').exists(), f'exit {code}, {took:.0f}s')
    report('it was started, and said so', marker.exists()
           and '--updated 9.9.9 --update-marker' in (install / 'launches.txt').read_text())
    report('a portable copy keeps its data', (install / 'data' / 'tasks.json').exists()
           and (install / 'portable.txt').exists())
    report('what was left running in the old folder is stopped', left_running.poll() is not None
           and 'stopping' in log, 'aria2c stand-in')
    report('the old version is cleared away', not (base / 'Grabbit.previous').exists())
    left_running.kill()

    print('\na new version that dies on start')
    base = scratch / 'rollback'
    install, staged = base / 'Grabbit', base / 'Grabbit.update'
    program(install, stub, 'ok')
    (install / 'old.txt').write_text('old')
    (install / 'data').mkdir()
    (install / 'data' / 'tasks.json').write_text('{"tasks": []}')
    program(staged, stub, 'die')
    (staged / 'new.txt').write_text('new')
    code, marker, log = run_helper(scratch, install, staged, '9.9.9', old_copy().pid)
    report('the previous version is put back', code == 1 and (install / 'old.txt').exists(),
           f'exit {code}')
    report('with its data', (install / 'data' / 'tasks.json').exists())
    report('and started, told the update failed', '--update-failed 9.9.9' in wait_for_launch(install))
    report('the broken one is kept aside, not deleted', (base / 'Grabbit.failed' / 'new.txt').exists())
    report('the log says what happened', 'did not start' in log, log.strip().splitlines()[-1] if log else '')

    print('\na Grabbit.exe sharing its folder with other things')
    # Unpacked into a project folder, say. The swap would rename that folder
    # and delete it once the new version was up - so it must not start.
    base = scratch / 'shared'
    install, staged = base / 'Project', base / 'Project.update'
    program(install, stub, 'ok')
    (install / 'old.txt').write_text('old')
    (install / 'my-work.txt').write_text('not Grabbit')
    (install / '.git').mkdir()
    program(staged, stub, 'ok')
    (staged / 'new.txt').write_text('new')
    code, marker, log = run_helper(scratch, install, staged, '9.9.9', old_copy().pid)
    report('it refuses to touch the folder', code == 1 and (install / 'my-work.txt').exists()
           and (install / '.git').is_dir() and (install / 'old.txt').exists()
           and not (base / 'Project.previous').exists(), f'exit {code}')
    report('and says why', 'holds more than Grabbit' in log and 'my-work.txt' in log,
           log.strip().splitlines()[-1] if log else 'no log')
    report('the copy that was running is started again', '--update-failed 9.9.9' in wait_for_launch(install))
    report('the app itself refuses first', 'shares its folder' in (selfupdate.blocker(install) or ''),
           (selfupdate.blocker(install) or 'no objection')[:70])

    print('\nwhat earlier updates left behind')
    base = scratch / 'leftovers'
    install = base / 'Grabbit'
    program(install, stub, 'ok')
    ours = base / 'Grabbit.failed'
    program(ours, stub, 'ok')
    holding_data = base / 'Grabbit.previous'
    program(holding_data, stub, 'ok')
    (holding_data / 'data').mkdir()
    (holding_data / 'data' / 'tasks.json').write_text('{"tasks": []}')
    not_ours = base / 'Grabbit.update'
    not_ours.mkdir()
    (not_ours / 'something-else.txt').write_text('keep me')
    shipped, work = selfupdate.SHIPPED, selfupdate.WORK
    selfupdate.SHIPPED, selfupdate.WORK = shipped + STUB_FILES, base / 'work'
    try:
        report('a clean install has nothing in the way', selfupdate.blocker(install) is None,
               selfupdate.blocker(install) or '')
        selfupdate.cleanup(install)
    finally:
        selfupdate.SHIPPED, selfupdate.WORK = shipped, work
    report('an old copy of Grabbit is cleared away', not ours.exists())
    report('one still holding data is kept', (holding_data / 'data' / 'tasks.json').exists())
    report('a folder that is not Grabbit is kept', (not_ours / 'something-else.txt').exists())


# ---------------------------------------------------------------- the real app

class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def check_real(scratch: Path) -> None:
    print('\nthe real Grabbit.exe, updating itself from a stand-in for GitHub')
    dist = ROOT / 'dist' / 'Grabbit'
    version = updates.APP_VERSION
    archive = ROOT / 'dist' / f'Grabbit-{version}-win64.zip'
    if not (dist / 'Grabbit.exe').exists() or not archive.exists():
        print(f'  [SKIP] needs dist\\Grabbit and dist\\{archive.name} - run build_app.ps1 and '
              'release.ps1 --dry-run first')
        return

    # A release, as GitHub would serve it: the zip, and a manifest signed with
    # a key made for this run.
    site = scratch / 'site'
    folder = site / 'releases' / 'download' / f'v{version}'
    latest = site / 'releases' / 'latest' / 'download'
    folder.mkdir(parents=True)
    latest.mkdir(parents=True)
    shutil.copyfile(archive, folder / archive.name)
    secret = ed25519.generate_secret()
    manifest = updates.build_manifest(version, notes='A test release.', published_at='now',
                                      desktop=folder / archive.name, android=None)
    (latest / updates.MANIFEST_NAME).write_bytes(manifest)
    (latest / updates.SIGNATURE_NAME).write_text(updates.sign_manifest(manifest, secret.hex()))
    server = HTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()

    # An "installed" copy, a version behind as far as it knows.
    install = scratch / 'installed' / 'Grabbit'
    shutil.copytree(dist, install)
    # Marked in a file Grabbit ships: anything else would, rightly, stop the swap.
    (install / 'build-commit.txt').write_text('the old copy')
    # Portable, so it keeps its data beside itself - an empty list of its own
    # rather than anyone's real downloads - and so the data has to survive
    # the swap, which is checked below.
    (install / 'portable.txt').write_text('')
    env = {**selfupdate.clean_environment(),
           updates.ENV_SITE: f'http://127.0.0.1:{server.server_port}',
           updates.ENV_KEY: ed25519.public_key(secret).hex(),
           updates.ENV_CURRENT: '1.0.0'}
    shutil.rmtree(selfupdate.WORK, ignore_errors=True)
    old = subprocess.Popen([str(install / 'Grabbit.exe'), '--install-update'], env=env)

    marker = selfupdate.marker_path(version)
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(2)
    old_gone = old.poll() is not None
    time.sleep(3)
    log = selfupdate.log_path().read_text(encoding='utf-8', errors='replace') \
        if selfupdate.log_path().exists() else ''
    report('it downloaded, checked, unpacked and handed over', old_gone and marker.exists(),
           f'old copy exited: {old_gone}')
    report('the folder now holds the new version', (install / 'Grabbit.exe').exists()
           and (install / 'build-commit.txt').read_text() != 'the old copy')
    report('the new version came up', 'updated to' in log, log.strip().splitlines()[-1] if log else 'no log')
    report('nothing is left beside it', not install.with_name('Grabbit.previous').exists()
           and not install.with_name('Grabbit.update').exists())
    report('a portable copy keeps its data across it', (install / 'portable.txt').exists()
           and (install / 'data').is_dir(), ', '.join(sorted(p.name for p in (install / 'data').iterdir()))
           if (install / 'data').is_dir() else 'no data folder')

    # Stop the updated copy, wherever it is now.
    subprocess.run(['powershell', '-NoProfile', '-Command',
                    f"Get-Process | Where-Object {{ try {{ $_.Path -like '{install}\\*' }} catch {{ $false }} }}"
                    ' | Stop-Process -Force'], capture_output=True)
    server.shutdown()


def main():
    if sys.platform != 'win32':
        print('This checks the Windows installer; run it on Windows.')
        return 0
    scratch = Path(tempfile.mkdtemp(prefix='grabbit-selfupdate-'))
    try:
        stub = scratch / 'stub.exe'
        compile_stub(stub)
        check_swap(scratch, stub)
        if '--real' in sys.argv:
            check_real(scratch)
    finally:
        time.sleep(1)
        shutil.rmtree(scratch, ignore_errors=True)

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
