r"""suitelog: a log file for a windowed suite app's output and crashes.

SHARED FILE - the same file sits in the suite's Qt apps (Keytap, Dropshift,
Grabbit). This copy, in Hanami, is the original: change it here and copy it
over; Hanami's tests/check_copies.py says whether they match.

A windowed exe (or pythonw) has no stderr. What it prints, and the tracebacks
Python writes, go nowhere. A native crash, such as one inside a COM or Qt call,
ends the process without a word. Call start(app) first thing in main(); it
sends all three to

    %LOCALAPPDATA%\<App>\<app>.log

and turns on faulthandler there, so a crash leaves every thread's stack in the
file. Run from a console nothing changes (`python -X faulthandler` shows a
crash's stack there).

Each start writes a dated line first. A log past 1 MB at a start is kept as
<app>.old.log, and a fresh one begins.

On Windows, faulthandler also writes "Windows fatal exception" for an access
violation that something below Python catches and survives (shell extensions
do this). If more lines follow it from the same pid, the app survived. A crash
is the last thing a run writes.
"""

from __future__ import annotations

import faulthandler
import os
import sys
import time
from pathlib import Path

LIMIT = 1_000_000


def start(app: str, folder: Path | None = None) -> Path | None:
    r"""Send output and native crashes to <folder>\<app>.log when there is no stderr.

    The folder defaults to %LOCALAPPDATA%\<App>. Returns the log's path, or None
    when output stays where it is.
    """
    if sys.stderr is not None:
        return None
    folder = Path(folder or Path(os.environ.get('LOCALAPPDATA', str(Path.home()))) / app)
    path = folder / f'{app.lower()}.log'
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if path.is_file() and path.stat().st_size > LIMIT:
            try:
                os.replace(path, path.with_suffix('.old.log'))
            except OSError:
                pass                # another copy has it open: it starts afresh next time
        log = open(path, 'a', encoding='utf-8', errors='backslashreplace', buffering=1)  # noqa: SIM115
    except OSError:
        return None
    log.write(f'--- {time.strftime("%Y-%m-%d %H:%M:%S")}  pid {os.getpid()}\n')
    sys.stdout = sys.stderr = log
    faulthandler.enable(log)
    return path
