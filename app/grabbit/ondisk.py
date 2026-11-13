"""What a torrent finds already on disk where it is to be saved, before aria2 is given it.

aria2 refuses a torrent whose file is already there without its control file (<name>.aria2):
"exists, but a control file(*.aria2) does not exist. Download was canceled in order to prevent your
file from being truncated". That file may be this same download, cut off - its control file
deleted, the download removed and added again, a copy that stopped part way. Then the pieces
already there are good and only the rest need fetching, and check-integrity has aria2 hash every
piece and fetch only those that fail. Or it may be some other file of the same name, which must be
left alone. A few of its pieces, hashed here against the torrent's own hashes, tell which.

Used by the desktop engine and the phone's (android\\grabbit_mobile\\engine.py) alike.
"""

import hashlib
import os

from .torrentmeta import bdecode, parse_torrent

NOTHING = 'nothing'     # none of its files are there: a plain start
RESUME = 'resume'       # its control file is there: aria2 carries on by itself
CHECK = 'check'         # its own pieces, or nothing written yet: have aria2 check and fill in
OTHER = 'other'         # something else of the same name: leave it be

SAMPLES = 12                    # pieces hashed at most
BUDGET = 256 * 1024 * 1024      # bytes read at most


def control_file(save_dir: str, meta) -> str:
    """aria2's control file for a torrent: its base path (dir/name) plus .aria2."""
    return os.path.join(save_dir, meta.name) + '.aria2'


def files_of(save_dir: str, meta) -> list:
    """(aria2 index, path on disk, start within the torrent's bytes, length) for each file."""
    base = os.path.join(save_dir, meta.name)
    result, start = [], 0
    for entry in meta.files:
        path = base if meta.is_single_file else os.path.join(base, *entry.path.split('/'))
        result.append((entry.index, path, start, entry.length))
        start += entry.length
    return result


def chosen(select_files: str) -> set:
    """aria2's select-file ("1,3-5") as a set of indexes; empty means every file."""
    picked = set()
    for part in (select_files or '').split(','):
        part = part.strip()
        if '-' in part:
            first, _, last = part.partition('-')
            picked.update(range(int(first), int(last) + 1))
        elif part:
            picked.add(int(part))
    return picked


def needs_look(save_dir: str, data: bytes) -> bool:
    """Whether aria2 would find this torrent's data there without its control file. Cheap: only
    asks whether paths exist, so it can run on the window's thread."""
    try:
        meta = parse_torrent(data)
    except Exception:
        return False                    # aria2 will say what is wrong with it
    if not meta.name or os.path.exists(control_file(save_dir, meta)):
        return False
    return any(os.path.isfile(path) for _, path, _, _ in files_of(save_dir, meta))


def _piece(files: list, index: int, piece_length: int, total: int) -> bytes | None:
    """A piece's bytes as they are on disk, or None when a file it lies in is missing or ends
    before it does."""
    start = index * piece_length
    end = min(start + piece_length, total)
    block = bytearray()
    for _, path, file_start, length in files:
        low, high = max(start, file_start), min(end, file_start + length)
        if low >= high:
            continue
        try:
            if os.path.getsize(path) < high - file_start:
                return None
            with open(path, 'rb') as handle:
                handle.seek(low - file_start)
                block += handle.read(high - low)
        except FileNotFoundError:
            return None
    return bytes(block) if len(block) == end - start else None


def look(save_dir: str, data: bytes, select_files: str = '') -> str:
    """NOTHING, RESUME, CHECK or OTHER for this torrent saved into save_dir. Reads at most BUDGET
    bytes; run it off the window's thread. Raises OSError when what is there can't be read."""
    meta = parse_torrent(data)
    if os.path.exists(control_file(save_dir, meta)):
        return RESUME
    picked = chosen(select_files)
    files = files_of(save_dir, meta)
    present = [f for f in files if (not picked or f[0] in picked) and os.path.isfile(f[1])]
    if not present:
        return NOTHING
    info = bdecode(data)[0][b'info']
    hashes = info.get(b'pieces') or b''
    size = meta.piece_length
    total = sum(f[3] for f in files)
    if not size or not hashes:
        return OTHER

    # Pieces that lie in files that are there, as far as those files go.
    readable = []
    for _, path, start, length in present:
        have = min(length, os.path.getsize(path))
        if have <= 0:
            continue
        for index in range(start // size, (start + have - 1) // size + 1):
            if (index + 1) * size <= start + have or (index + 1) * size >= total:
                readable.append(index)
    readable = sorted(set(readable))

    # Spread over the whole file: a download cut off has its start, a torrent's pieces land anywhere.
    step = max(1, len(readable) / SAMPLES)
    picks = sorted({readable[min(len(readable) - 1, int(i * step))] for i in range(SAMPLES)}) if readable else []
    spent, hashed, written_elsewhere = 0, 0, False
    for index in picks:
        if spent >= BUDGET:
            break
        block = _piece(files, index, size, total)
        if block is None:
            continue
        spent += len(block)
        hashed += 1
        if hashlib.sha1(block).digest() == hashes[index * 20:index * 20 + 20]:  # noqa: S324 - BitTorrent v1
            return CHECK
        if block.strip(b'\0'):
            written_elsewhere = True    # real data that isn't this torrent's
    if not hashed:
        # Files there with something in them, but not one whole piece to go on: they are kept.
        # Empty files have nothing to lose.
        return OTHER if any(os.path.getsize(f[1]) for f in present) else CHECK
    # Only zeros (made full size, nothing written yet) costs nothing to fill in.
    return OTHER if written_elsewhere else CHECK
