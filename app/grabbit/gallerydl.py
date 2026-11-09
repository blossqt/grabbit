"""Photo posts on the sites yt-dlp does not cover, via gallery-dl.

gallery-dl runs as a separate program (tools/gallery-dl.exe) rather than being
imported: Grabbit only asks it to print the list of files behind a link and
reads that JSON back. It downloads nothing itself - every URL it finds goes to
aria2 like any other file. On a phone it is a package that the APK's own
Python runs, in a process of its own, which comes to the same thing (see
android/grabbit_mobile/bootstrap.py).

Keeping it at arm's length also keeps the licences apart: gallery-dl is
GPL-2.0-only, and calling a separate program is plain aggregation.
"""

import json
import logging
import os
import subprocess
import urllib.request
from urllib.parse import urlparse

from .mediaitems import MediaItem, ProbeResult
from .pagescrape import AUDIO_EXTS, VIDEO_EXTS
from .paths import find_tool
from .util import CREATE_NO_WINDOW, site_name

log = logging.getLogger(__name__)

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif', 'heic', 'tiff', 'svg'}
UNSUPPORTED_URL = 64          # gallery-dl's exit code for "not one of my sites"
TIMEOUT = 90

# Message types gallery-dl prints with --dump-json.
DIRECTORY, URL = 2, 3
# Its catch-all for any address ending in a picture's or a video's extension.
# That is a file, not a post: left to the file check, which knows its real type.
DIRECT_LINK = 'directlink'
FAILED = -1                   # the message saying why it stopped
GOFILE_ACCOUNTS = 'https://api.gofile.io/accounts'


# How to run it, where it is not an executable of its own: the command and
# its environment. The phone fills these in; the desktop leaves them alone.
COMMAND: list | None = None
ENVIRONMENT: dict | None = None


def executable() -> str | None:
    return find_tool('gallery-dl')


def command() -> list | None:
    """What starts gallery-dl here, or None if this copy has none."""
    if COMMAND:
        return list(COMMAND)
    exe = executable()
    return [exe] if exe else None


def available() -> bool:
    return bool(command())


def _is_gofile(url: str) -> bool:
    return (urlparse(url).hostname or '').lower() in ('gofile.io', 'www.gofile.io')


def _gofile_visitor() -> str:
    """The token Gofile gives anyone who opens its site. Its files are only served
    with it, and gallery-dl keeps the one it makes to itself, so Grabbit asks for one,
    gives it to gallery-dl and sends it with each download."""
    root = 'https://gofile.io'
    request = urllib.request.Request(GOFILE_ACCOUNTS, data=b'', method='POST', headers={
        'User-Agent': 'Mozilla/5.0', 'Accept': '*/*', 'Origin': root, 'Referer': root + '/'})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)['data']['token']
    except Exception as exc:
        log.info('Gofile gave no visitor token: %s', exc)
        return ''


def _arguments(settings, max_items: int, dump: str = '--dump-json') -> list:
    args = ['--quiet', dump, '--range', f'1-{max_items}']
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


def _group(ext: str) -> str:
    if ext in VIDEO_EXTS:
        return 'video'
    if ext in AUDIO_EXTS:
        return 'audio'
    return 'image' if ext in IMAGE_EXTS else 'file'


def _item(url: str, kwdict: dict, index: int, referer: str, headers: dict | None = None) -> MediaItem:
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
        # Gofile's says "size"; elsewhere that can be something else.
        filesize=number('filesize') or (number('size') if kwdict.get('category') == 'gofile' else 0) or None,
        ext=ext,
        direct_url=url,
        filename=f'{name}.{ext}'[:180],
        group=_group(ext),
        headers={'Referer': referer, **(headers or {})},
        index=index,
    )


def _who(value) -> str:
    """A person's name from gallery-dl's metadata - where some sites give a
    whole profile (X's does, bio and all) rather than a name."""
    if isinstance(value, dict):
        value = value.get('name') or value.get('nick') or value.get('username')
    return str(value) if value else ''


def probe(url: str, settings, max_items: int = 400) -> ProbeResult | None:
    """Ask gallery-dl what is behind a link. None means 'not a site it knows'."""
    run = command()
    if not run:
        return None

    gofile, sent, options = _is_gofile(url), {}, []
    if gofile:
        token = _gofile_visitor()
        if token:
            sent, options = {'Cookie': f'accountToken={token}'}, ['-o', f'api-token={token}']

    # An album's folders inside folders are links of their own: followed, for Gofile's.
    dump = '--resolve-json' if gofile else '--dump-json'
    try:
        finished = subprocess.run(
            [*run, *_arguments(settings, max_items, dump), *options, url],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=TIMEOUT, creationflags=CREATE_NO_WINDOW, env=ENVIRONMENT)
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
    known = site_name(url)             # "X" for x.com, where gallery-dl still says twitter
    result = ProbeResult(url=url, kind='gallery', site=known or parsed.netloc)
    items: list = []
    failure = ''
    for message in messages:
        if not isinstance(message, list) or not message:
            continue
        if isinstance(message[-1], dict) and message[-1].get('category') == DIRECT_LINK:
            return None
        if message[0] == FAILED and len(message) >= 2 and isinstance(message[1], dict):
            failure = str(message[1].get('message') or '')
        elif message[0] == URL and len(message) >= 3:
            items.append(_item(message[1], message[2] or {}, len(items) + 1, referer, sent))
        elif message[0] == DIRECTORY and len(message) >= 2:
            meta = message[1] or {}
            if not known:
                result.site = str(meta.get('category') or result.site).title()
            result.uploader = (_who(meta.get('user')) or _who(meta.get('username'))
                               or _who(meta.get('author')) or result.uploader)
            # A post's own words, where it has no title: X's tweets, say.
            words = meta.get('title') or meta.get('description') or meta.get('content')
            result.title = ' '.join(str(words).split())[:200] if words else result.title
            if gofile and not result.title and meta.get('name') != 'root':     # the album's, not a folder in it
                result.title = str(meta.get('name') or '')[:200]

    if not items:
        error = (finished.stderr or '').strip().splitlines()
        if error:
            log.info('gallery-dl found nothing: %s', error[-1][:160])
        if gofile and failure:              # a page of the site's own would say no more
            result.error = f'Gofile: {failure}'
            return result
        return None

    result.items = items
    result.title = result.title or f'{result.site} post'
    result.thumbnail = next((i.thumbnail for i in items if i.thumbnail), '')
    return result
