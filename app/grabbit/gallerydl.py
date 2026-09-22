"""Photo posts on the sites yt-dlp does not cover, via gallery-dl.

gallery-dl runs as a separate program (tools/gallery-dl.exe) rather than being
imported: Grabbit only asks it to print the list of files behind a link and
reads that JSON back. It downloads nothing itself - every URL it finds goes to
aria2 like any other file.

Keeping it at arm's length also keeps the licences apart: gallery-dl is
GPL-2.0-only, and calling a separate program is plain aggregation.
"""

import json
import logging
import os
import subprocess
from urllib.parse import urlparse

from .mediaitems import MediaItem, ProbeResult
from .paths import find_tool
from .util import CREATE_NO_WINDOW, site_name

log = logging.getLogger(__name__)

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif', 'heic', 'tiff', 'svg'}
UNSUPPORTED_URL = 64          # gallery-dl's exit code for "not one of my sites"
TIMEOUT = 90

# Message types gallery-dl prints with --dump-json.
DIRECTORY, URL = 2, 3


def executable() -> str | None:
    return find_tool('gallery-dl')


def available() -> bool:
    return bool(executable())


def _arguments(settings, max_items: int) -> list:
    args = ['--quiet', '--dump-json', '--range', f'1-{max_items}']
    cookies_file = getattr(settings, 'cookies_file', '')
    browser = getattr(settings, 'cookies_browser', '')
    if cookies_file and os.path.isfile(cookies_file):
        args += ['--cookies', cookies_file]
    elif browser:
        args += ['--cookies-from-browser', browser]
    proxy = getattr(settings, 'proxy', '')
    if proxy:
        args += ['--proxy', proxy]
    return args


def _extension(url: str, kwdict: dict) -> str:
    ext = str(kwdict.get('extension') or '').lower()
    if not ext:
        ext = os.path.splitext(urlparse(url).path)[1].lstrip('.').lower()
    return ext or 'jpg'


def _item(url: str, kwdict: dict, index: int, referer: str) -> MediaItem:
    ext = _extension(url, kwdict)
    preview = ''
    for key in ('preview_url', 'thumbnail', 'thumb', 'preview'):
        value = kwdict.get(key)
        if isinstance(value, str) and value.startswith('http'):
            preview = value
            break
    if not preview and ext in IMAGE_EXTS:
        preview = url

    name = str(kwdict.get('filename') or kwdict.get('title') or f'item{index}')
    def number(key):
        value = kwdict.get(key)
        return int(value) if isinstance(value, (int, float)) else 0

    return MediaItem(
        key=str(kwdict.get('id') or kwdict.get('num') or index),
        kind='image',                       # a direct file either way
        title=name[:120],
        thumbnail=preview,
        preview=preview,
        width=number('width'),
        height=number('height'),
        filesize=number('filesize') or None,
        ext=ext,
        direct_url=url,
        filename=f'{name}.{ext}'[:180],
        headers={'Referer': referer},
        index=index,
    )


def probe(url: str, settings, max_items: int = 400) -> ProbeResult | None:
    """Ask gallery-dl what is behind a link. None means 'not a site it knows'."""
    exe = executable()
    if not exe:
        return None

    try:
        finished = subprocess.run(
            [exe, *_arguments(settings, max_items), url],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=TIMEOUT, creationflags=CREATE_NO_WINDOW)
    except subprocess.TimeoutExpired:
        log.info('gallery-dl timed out on %s', url[:80])
        return None
    except OSError as exc:
        log.warning('could not run gallery-dl: %s', exc)
        return None

    if finished.returncode == UNSUPPORTED_URL:
        return None
    if not finished.stdout.strip():
        if finished.returncode:
            log.debug('gallery-dl said: %s', (finished.stderr or '').strip()[:200])
        return None

    try:
        messages = json.loads(finished.stdout)
    except ValueError:
        log.debug('gallery-dl returned something that is not JSON')
        return None

    parsed = urlparse(url)
    referer = f'{parsed.scheme}://{parsed.netloc}/'
    result = ProbeResult(url=url, kind='gallery', site=site_name(url) or parsed.netloc)
    items: list = []
    for message in messages:
        if not isinstance(message, list) or not message:
            continue
        if message[0] == URL and len(message) >= 3:
            items.append(_item(message[1], message[2] or {}, len(items) + 1, referer))
        elif message[0] == DIRECTORY and len(message) >= 2:
            meta = message[1] or {}
            result.site = str(meta.get('category') or result.site).title()
            result.uploader = str(meta.get('user') or meta.get('username')
                                  or meta.get('author') or result.uploader or '')
            result.title = str(meta.get('title') or meta.get('description')
                               or result.title or '')

    if not items:
        error = (finished.stderr or '').strip().splitlines()
        if error:
            log.info('gallery-dl found nothing: %s', error[-1][:160])
        return None

    result.items = items
    result.title = result.title or f'{result.site} post'
    result.thumbnail = next((i.thumbnail for i in items if i.thumbnail), '')
    return result
