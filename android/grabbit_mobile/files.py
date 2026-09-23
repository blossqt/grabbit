"""What a finished download left on the phone: the files to open and share.

A video, a photo or a plain file is one file, and the download says which. A
torrent is whichever of its files were chosen, laid out as its metadata says
- a file of its own for a one-file torrent, a folder for the rest. The list
comes from the torrent rather than from the folder, because aria2 writes the
edges of the files next to the chosen ones, and those are not downloads
anyone asked for.
"""

import os

from grabbit.tasks import State
from grabbit.torrentmeta import parse_torrent


def finished(task) -> bool:
    """Whether a download is whole on the phone: done, giving back, or
    stopped after it was."""
    if task.state in (State.COMPLETED, State.SEEDING):
        return True
    return task.state == State.PAUSED and task.total > 0 and task.done >= task.total


def finished_files(task) -> list:
    """The files a download made that are still there, in its own order."""
    if task.is_torrent:
        found = [path for path in _torrent_files(task) if os.path.isfile(path)]
        if found:
            return found
    target = task.file_path
    if not target and task.save_dir and (task.out or (task.is_torrent and task.name)):
        target = os.path.join(task.save_dir, task.out or task.name)
    if target and os.path.isdir(target):
        return _inside(target)
    return [target] if target and os.path.isfile(target) else []


def _torrent_files(task) -> list:
    try:
        with open(task.torrent_file, 'rb') as handle:
            info = parse_torrent(handle.read())
    except (OSError, ValueError):
        return []
    wanted = _chosen(task.select_files)
    base = task.save_dir if info.is_single_file else os.path.join(task.save_dir, info.name)
    return [os.path.join(base, *entry.path.split('/')) for entry in info.files
            if wanted is None or entry.index in wanted]


def _chosen(spec: str):
    """aria2's --select-file ('1,3-5'), as the indexes it names; None for all."""
    if not spec:
        return None
    wanted = set()
    for part in spec.split(','):
        first, _, last = part.partition('-')
        try:
            wanted.update(range(int(first), int(last or first) + 1))
        except ValueError:
            return None
    return wanted


def _inside(folder: str) -> list:
    found = []
    for where, folders, names in os.walk(folder):
        folders.sort()
        found.extend(os.path.join(where, name) for name in sorted(names)
                     if not name.endswith('.aria2'))
    return found
