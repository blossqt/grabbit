"""Works out what a pasted link is: magnet, torrent, media page, or plain file."""

import logging
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from . import gallerydl
from . import media as media_mod
from . import metalink, pagescrape
from .torrentmeta import TorrentInfo, parse_magnet, parse_torrent
from .util import safe_filename, site_name

log = logging.getLogger(__name__)

MAX_TORRENT_BYTES = 8 * 1024 * 1024
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

KIND_MAGNET = 'magnet'
KIND_TORRENT = 'torrent'
KIND_METALINK = 'metalink'  # mirrors + checksums for one or more files
KIND_MEDIA = 'media'        # single video/audio
KIND_GALLERY = 'gallery'    # photos (carousel / slideshow / scraped page)
KIND_PLAYLIST = 'playlist'  # several videos
KIND_FILE = 'file'
KIND_ERROR = 'error'


@dataclass
class Analysis:
    url: str
    kind: str = KIND_FILE
    title: str = ''
    site: str = ''
    error: str = ''
    hint: str = ''
    magnet: dict = field(default_factory=dict)
    torrent_data: bytes = b''
    torrent_info: TorrentInfo | None = None
    metalink_files: list = field(default_factory=list)
    probe: object = None          # media.ProbeResult
    filename: str = ''
    filesize: int = 0
    content_type: str = ''


def _request(url: str, method: str = 'GET', extra: dict | None = None):
    headers = {'User-Agent': _UA, 'Accept': '*/*', **(extra or {})}
    return urllib.request.Request(url, headers=headers, method=method)


def http_head(url: str, timeout: float = 15) -> dict:
    """Content-Type / length / filename, without downloading the body."""
    info = {'status': 0, 'content_type': '', 'length': 0, 'filename': '', 'url': url}
    for method in ('HEAD', 'GET'):
        try:
            extra = {'Range': 'bytes=0-0'} if method == 'GET' else None
            with urllib.request.urlopen(_request(url, method, extra), timeout=timeout) as response:
                headers = response.headers
                info['status'] = response.status
                info['url'] = response.url
                info['content_type'] = (headers.get('Content-Type') or '').split(';')[0].strip().lower()
                length = headers.get('Content-Length')
                if method == 'GET' and headers.get('Content-Range'):
                    total = headers['Content-Range'].rsplit('/', 1)[-1]
                    length = total if total.isdigit() else length
                info['length'] = int(length) if (length or '').isdigit() else 0
                disposition = headers.get('Content-Disposition') or ''
                match = re.search(r"filename\*=(?:UTF-8'')?([^;]+)|filename=\"?([^\";]+)\"?",
                                  disposition, re.IGNORECASE)
                if match:
                    raw = match.group(1) or match.group(2) or ''
                    info['filename'] = safe_filename(urllib.parse.unquote(raw.strip()))
                return info
        except urllib.error.HTTPError as exc:
            if method == 'GET' or exc.code not in (403, 405, 501):
                info['status'] = exc.code
                info['error'] = f'HTTP {exc.code}'
                if exc.code not in (403, 405, 501):
                    return info
        except Exception as exc:
            info['error'] = str(exc)
            return info
    return info


def fetch_bytes(url: str, limit: int = MAX_TORRENT_BYTES, timeout: float = 30) -> bytes:
    with urllib.request.urlopen(_request(url), timeout=timeout) as response:
        return response.read(limit)


def name_from_url(url: str) -> str:
    path = urllib.parse.urlparse(url).path
    name = safe_filename(urllib.parse.unquote(os.path.basename(path)))
    return name or ''


_EXTRACTOR_CACHE = None


def matching_extractor(url: str) -> str:
    """ie_key of the yt-dlp extractor for this URL, ignoring the catch-all one."""
    global _EXTRACTOR_CACHE
    if _EXTRACTOR_CACHE is None:
        try:
            from yt_dlp.extractor import gen_extractor_classes
            _EXTRACTOR_CACHE = [ie for ie in gen_extractor_classes()
                                if ie.ie_key() not in ('Generic',) and ie.working()]
        except Exception as exc:
            log.warning('could not list extractors: %s', exc)
            _EXTRACTOR_CACHE = []
    for extractor in _EXTRACTOR_CACHE:
        try:
            if extractor.suitable(url):
                return extractor.ie_key()
        except Exception:
            continue
    return ''


def _from_probe(url: str, result, analysis: Analysis) -> Analysis:
    analysis.probe = result
    analysis.title = result.title
    analysis.site = result.site or site_name(url)
    if result.error:
        analysis.kind = KIND_ERROR
        analysis.error = result.error
    elif result.kind == 'gallery':
        analysis.kind = KIND_GALLERY
    elif result.kind == 'playlist':
        analysis.kind = KIND_PLAYLIST
    else:
        analysis.kind = KIND_MEDIA
    return analysis


def analyze(url: str, settings) -> Analysis:
    """Inspect one link. Runs off the GUI thread; never raises."""
    url = (url or '').strip()
    analysis = Analysis(url=url, site=site_name(url))

    if url.lower().startswith('magnet:'):
        info = parse_magnet(url)
        analysis.kind = KIND_MAGNET
        analysis.magnet = info
        analysis.title = info.get('name') or f'Magnet {info.get("info_hash", "")[:10]}'
        if not info.get('info_hash'):
            analysis.kind = KIND_ERROR
            analysis.error = 'This magnet link has no BitTorrent v1 hash that aria2 can use.'
        return analysis

    if os.path.isfile(url) and url.lower().endswith('.torrent'):
        try:
            data = open(url, 'rb').read()
            analysis.kind = KIND_TORRENT
            analysis.torrent_data = data
            analysis.torrent_info = parse_torrent(data)
            analysis.title = analysis.torrent_info.name
        except Exception as exc:
            analysis.kind, analysis.error = KIND_ERROR, f'Could not read that torrent: {exc}'
        return analysis

    if not re.match(r'^(https?|ftp|sftp)://', url, re.IGNORECASE):
        analysis.kind = KIND_ERROR
        analysis.error = 'Not a link Grabbit understands.'
        return analysis

    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith('.torrent'):
        try:
            data = fetch_bytes(url)
            analysis.kind = KIND_TORRENT
            analysis.torrent_data = data
            analysis.torrent_info = parse_torrent(data)
            analysis.title = analysis.torrent_info.name
            return analysis
        except Exception as exc:
            analysis.kind, analysis.error = KIND_ERROR, f'Could not fetch that torrent: {exc}'
            return analysis

    if url.lower().startswith(('ftp://', 'sftp://')):
        analysis.kind = KIND_FILE
        analysis.filename = name_from_url(url) or 'download'
        analysis.title = analysis.filename
        return analysis

    if path.endswith(('.metalink', '.meta4')):
        found = _try_metalink(url, analysis)
        if found is not None:
            return found

    extractor = matching_extractor(url)
    if extractor:
        result = media_mod.probe(url, settings)
        if result.items and not result.error:
            return _from_probe(url, result, analysis)
        # A site yt-dlp knows, but no video on this particular page - a photo
        # post on X, say. gallery-dl handles those.
        photos = _try_gallery_dl(url, settings)
        if photos is not None:
            return _from_probe(url, photos, analysis)
        head = http_head(url)
        if head.get('content_type') and not head['content_type'].startswith('text/'):
            return _as_file(url, head, analysis)
        return _from_probe(url, result, analysis)

    photos = _try_gallery_dl(url, settings)
    if photos is not None:
        return _from_probe(url, photos, analysis)

    head = http_head(url)
    content_type = head.get('content_type', '')
    if content_type in ('application/x-bittorrent', 'application/octet-stream') and path.endswith('.torrent'):
        try:
            data = fetch_bytes(head.get('url') or url)
            analysis.kind = KIND_TORRENT
            analysis.torrent_data = data
            analysis.torrent_info = parse_torrent(data)
            analysis.title = analysis.torrent_info.name
            return analysis
        except Exception:
            pass

    if 'metalink' in content_type:
        found = _try_metalink(head.get('url') or url, analysis)
        if found is not None:
            return found

    if content_type.startswith('text/html') or (not content_type and not head.get('length')):
        result = media_mod.probe(url, settings)          # yt-dlp's generic extractor
        if result.items and not result.error:
            return _from_probe(url, result, analysis)
        scraped = pagescrape.scrape(url, settings)       # read the page ourselves
        if scraped.items:
            return _from_probe(url, scraped, analysis)
        analysis = _as_file(url, head, analysis)
        analysis.hint = ('No video, photos or files were found on this page - Grabbit would '
                         'just save the page itself.')
        return analysis

    return _as_file(url, head, analysis)


def _try_gallery_dl(url: str, settings):
    """A photo-first source, if gallery-dl recognises the link."""
    if not gallerydl.available():
        return None
    try:
        result = gallerydl.probe(url, settings)
    except Exception as exc:
        log.debug('gallery-dl failed for %s: %s', url, exc)
        return None
    return result if (result and result.items) else None


def _try_metalink(url: str, analysis: Analysis):
    try:
        data = fetch_bytes(url)
        files = metalink.parse(data)
    except Exception as exc:
        log.debug('metalink parsing failed: %s', exc)
        return None
    if not files:
        return None
    analysis.kind = KIND_METALINK
    analysis.metalink_files = files
    analysis.title = files[0].name if len(files) == 1 else f'{len(files)} files'
    analysis.filesize = sum(f.size for f in files)
    return analysis


def _as_file(url: str, head: dict, analysis: Analysis) -> Analysis:
    analysis.kind = KIND_FILE
    analysis.filename = head.get('filename') or name_from_url(url) or 'download'
    analysis.filesize = head.get('length') or 0
    analysis.content_type = head.get('content_type', '')
    analysis.title = analysis.filename
    if head.get('error') and not head.get('status'):
        analysis.kind = KIND_ERROR
        analysis.error = f'Could not reach that link: {head["error"]}'
    return analysis
