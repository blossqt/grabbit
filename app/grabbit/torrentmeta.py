"""Reading .torrent files and magnet links, so the UI can show contents
before anything is handed to aria2."""

import base64
import hashlib
from dataclasses import dataclass, field
from urllib.parse import parse_qs, unquote, urlparse


class BencodeError(ValueError):
    pass


def bdecode(data: bytes, pos: int = 0):
    """Decode one bencoded value; returns (value, next position)."""
    if pos >= len(data):
        raise BencodeError('unexpected end of data')
    char = data[pos:pos + 1]
    if char == b'i':
        end = data.index(b'e', pos)
        return int(data[pos + 1:end]), end + 1
    if char == b'l':
        items, pos = [], pos + 1
        while data[pos:pos + 1] != b'e':
            value, pos = bdecode(data, pos)
            items.append(value)
        return items, pos + 1
    if char == b'd':
        result, pos = {}, pos + 1
        while data[pos:pos + 1] != b'e':
            key, pos = bdecode(data, pos)
            value, pos = bdecode(data, pos)
            result[key] = value
        return result, pos + 1
    if char.isdigit():
        colon = data.index(b':', pos)
        length = int(data[pos:colon])
        start = colon + 1
        return data[start:start + length], start + length
    raise BencodeError(f'bad bencode marker {char!r} at {pos}')


def _find_info_span(data: bytes) -> tuple[int, int] | None:
    """Byte range of the raw 'info' dictionary (needed for the info hash)."""
    try:
        top, _ = bdecode(data)
        if not isinstance(top, dict) or b'info' not in top:
            return None
        pos = 1  # skip the leading 'd'
        while data[pos:pos + 1] != b'e':
            key, pos = bdecode(data, pos)
            start = pos
            _, pos = bdecode(data, pos)
            if key == b'info':
                return start, pos
        return None
    except (BencodeError, IndexError, ValueError):
        return None


def _text(value, encoding='utf-8') -> str:
    if isinstance(value, bytes):
        try:
            return value.decode(encoding, errors='replace')
        except LookupError:
            return value.decode('utf-8', errors='replace')
    return str(value or '')


@dataclass
class TorrentFile:
    index: int          # aria2 file index (1-based)
    path: str           # path inside the torrent
    length: int


@dataclass
class TorrentInfo:
    name: str = ''
    info_hash: str = ''
    total_size: int = 0
    files: list = field(default_factory=list)
    piece_length: int = 0
    num_pieces: int = 0
    private: bool = False
    comment: str = ''
    created_by: str = ''
    creation_date: int = 0
    trackers: list = field(default_factory=list)
    is_single_file: bool = True


def parse_torrent(data: bytes) -> TorrentInfo:
    top, _ = bdecode(data)
    if not isinstance(top, dict):
        raise BencodeError('not a torrent file')
    info = top.get(b'info')
    if not isinstance(info, dict):
        raise BencodeError('torrent has no info dictionary')

    encoding = _text(top.get(b'encoding') or b'utf-8') or 'utf-8'
    result = TorrentInfo()
    span = _find_info_span(data)
    if span:
        result.info_hash = hashlib.sha1(data[span[0]:span[1]]).hexdigest()  # noqa: S324 - BitTorrent v1
    result.name = _text(info.get(b'name.utf-8') or info.get(b'name'), encoding)
    result.piece_length = int(info.get(b'piece length') or 0)
    pieces = info.get(b'pieces') or b''
    result.num_pieces = len(pieces) // 20
    result.private = bool(info.get(b'private'))
    result.comment = _text(top.get(b'comment.utf-8') or top.get(b'comment'), encoding)
    result.created_by = _text(top.get(b'created by'), encoding)
    result.creation_date = int(top.get(b'creation date') or 0)

    trackers = []
    for tier in top.get(b'announce-list') or []:
        for tracker in (tier if isinstance(tier, list) else [tier]):
            url = _text(tracker, encoding)
            if url and url not in trackers:
                trackers.append(url)
    announce = _text(top.get(b'announce'), encoding)
    if announce and announce not in trackers:
        trackers.insert(0, announce)
    result.trackers = trackers

    files = info.get(b'files')
    if isinstance(files, list) and files:
        result.is_single_file = False
        for index, entry in enumerate(files, start=1):
            parts = entry.get(b'path.utf-8') or entry.get(b'path') or []
            path = '/'.join(_text(p, encoding) for p in parts)
            length = int(entry.get(b'length') or 0)
            result.files.append(TorrentFile(index, path, length))
            result.total_size += length
    elif b'length' in info:
        length = int(info[b'length'])
        result.files.append(TorrentFile(1, result.name, length))
        result.total_size = length
    elif isinstance(info.get(b'file tree'), dict):
        # BitTorrent v2 layout (aria2 only downloads v1/hybrid torrents)
        def walk(node, prefix):
            for key, value in node.items():
                name = _text(key, encoding)
                if isinstance(value, dict) and b'' in value:
                    length = int(value[b''].get(b'length') or 0)
                    result.files.append(TorrentFile(len(result.files) + 1, '/'.join([*prefix, name]), length))
                    result.total_size += length
                elif isinstance(value, dict):
                    walk(value, [*prefix, name])

        walk(info[b'file tree'], [])
        result.is_single_file = len(result.files) <= 1
    return result


def torrent_to_base64(data: bytes) -> str:
    return base64.b64encode(data).decode('ascii')


def parse_magnet(uri: str) -> dict:
    """info_hash / name / trackers from a magnet link ({} if it isn't one)."""
    if not uri.lower().startswith('magnet:'):
        return {}
    query = parse_qs(urlparse(uri).query)
    info_hash = ''
    for xt in query.get('xt', []):
        value = xt.lower()
        if value.startswith('urn:btih:'):
            digest = xt[9:]
            if len(digest) == 40:
                info_hash = digest.lower()
            elif len(digest) == 32:  # base32
                try:
                    info_hash = base64.b32decode(digest.upper()).hex()
                except Exception:
                    pass
            break
        if value.startswith('urn:btmh:'):  # v2 infohash, aria2 cannot use it
            info_hash = ''
    return {
        'info_hash': info_hash,
        'name': unquote(query.get('dn', [''])[0]),
        'trackers': query.get('tr', []),
    }


def select_file_spec(indexes: list[int], total: int) -> str:
    """aria2 --select-file value; '' means every file (aria2's default)."""
    chosen = sorted({i for i in indexes if 1 <= i <= total})
    if not chosen or len(chosen) == total:
        return ''
    parts, start, prev = [], chosen[0], chosen[0]
    for index in chosen[1:]:
        if index == prev + 1:
            prev = index
            continue
        parts.append(f'{start}-{prev}' if prev > start else str(start))
        start = prev = index
    parts.append(f'{start}-{prev}' if prev > start else str(start))
    return ','.join(parts)
