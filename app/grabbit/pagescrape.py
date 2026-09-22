"""Last resort: find the media on an ordinary web page.

When neither yt-dlp nor gallery-dl knows a site, Grabbit reads the page itself
and lists the videos, images, audio and documents it links to, so they land in
the same tick-box picker. Everything found is a direct file URL handed to
aria2 - no scripts from the page are executed.
"""

import html
import logging
import os
import re
import urllib.request
from urllib.parse import urljoin, urlparse

from .mediaitems import MediaItem, ProbeResult
from .util import host_of, safe_filename

log = logging.getLogger(__name__)

MAX_HTML = 4 * 1024 * 1024
_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
       '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')

IMAGE_EXTS = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'avif', 'tiff', 'heic'}
VIDEO_EXTS = {'mp4', 'webm', 'mkv', 'mov', 'm4v', 'avi', 'flv', 'ogv'}
AUDIO_EXTS = {'mp3', 'm4a', 'aac', 'ogg', 'opus', 'wav', 'flac'}
DOC_EXTS = {'pdf', 'epub', 'zip', 'rar', '7z', 'tar', 'gz', 'docx', 'xlsx', 'pptx', 'csv'}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS | AUDIO_EXTS | DOC_EXTS

# Site furniture rather than content.
JUNK = re.compile(r'(sprite|favicon|logo|icon[-_.]|avatar|spacer|pixel|blank|1x1|'
                  r'placeholder|loading|badge|emoji)', re.IGNORECASE)

_ATTR = r'''(?:"([^"]+)"|'([^']+)'|([^\s"'>]+))'''
PATTERNS = (
    re.compile(rf'<(?:img|source|video|audio|embed)[^>]+?\bsrc\s*=\s*{_ATTR}', re.IGNORECASE),
    re.compile(rf'<[^>]+?\bdata-(?:src|original|lazy-src|full|zoom-image)\s*=\s*{_ATTR}', re.IGNORECASE),
    re.compile(rf'<a[^>]+?\bhref\s*=\s*{_ATTR}', re.IGNORECASE),
    re.compile(rf'<meta[^>]+?(?:og:image|og:video|twitter:image)[^>]+?\bcontent\s*=\s*{_ATTR}',
               re.IGNORECASE),
    re.compile(rf'<link[^>]+?\brel\s*=\s*["\']?image_src["\']?[^>]+?\bhref\s*=\s*{_ATTR}',
               re.IGNORECASE),
)
SRCSET = re.compile(rf'\bsrcset\s*=\s*{_ATTR}', re.IGNORECASE)
TITLE = re.compile(r'<title[^>]*>(.*?)</title>', re.IGNORECASE | re.DOTALL)


def _fetch(url: str) -> tuple[str, str]:
    request = urllib.request.Request(url, headers={
        'User-Agent': _UA, 'Accept': 'text/html,application/xhtml+xml,*/*'})
    with urllib.request.urlopen(request, timeout=25) as response:
        charset = response.headers.get_content_charset() or 'utf-8'
        data = response.read(MAX_HTML)
        return data.decode(charset, errors='replace'), response.url


def _value(match) -> str:
    return html.unescape(next((g for g in match.groups() if g), '')).strip()


def _extension(url: str) -> str:
    path = urlparse(url).path
    return os.path.splitext(path)[1].lstrip('.').lower()


def _largest_from_srcset(value: str) -> str:
    best, best_width = '', -1
    for part in value.split(','):
        bits = part.strip().split()
        if not bits:
            continue
        width = 0
        if len(bits) > 1 and bits[1].endswith('w'):
            width = int(re.sub(r'\D', '', bits[1]) or 0)
        if width >= best_width:
            best, best_width = bits[0], width
    return best


def scrape(url: str, settings=None, max_items: int = 300) -> ProbeResult:
    """List every downloadable file an ordinary page points at."""
    result = ProbeResult(url=url, kind='gallery', site=host_of(url))
    try:
        page, final_url = _fetch(url)
    except Exception as exc:
        result.error = f'Could not read that page: {exc}'
        return result

    title_match = TITLE.search(page)
    result.title = html.unescape(title_match.group(1)).strip()[:160] if title_match else host_of(url)

    found: dict = {}
    def consider(raw: str):
        if not raw or raw.startswith(('data:', 'javascript:', 'mailto:', '#')):
            return
        absolute = urljoin(final_url, raw)
        if not absolute.startswith(('http://', 'https://')):
            return
        extension = _extension(absolute)
        if extension not in ALL_EXTS or JUNK.search(absolute):
            return
        found.setdefault(absolute.split('#')[0], extension)

    for pattern in PATTERNS:
        for match in pattern.finditer(page):
            consider(_value(match))
    for match in SRCSET.finditer(page):
        consider(_largest_from_srcset(_value(match)))

    order = {'video': 0, 'audio': 1, 'image': 2, 'file': 3}

    def category(extension: str) -> str:
        if extension in VIDEO_EXTS:
            return 'video'
        if extension in AUDIO_EXTS:
            return 'audio'
        if extension in IMAGE_EXTS:
            return 'image'
        return 'file'

    items = []
    for link, extension in list(found.items())[:max_items]:
        group = category(extension)
        name = safe_filename(os.path.basename(urlparse(link).path)) or f'file.{extension}'
        items.append(MediaItem(
            key=str(len(items) + 1),
            kind='image',                       # direct download either way
            title=name,
            thumbnail=link if group == 'image' else '',
            preview=link if group == 'image' else '',
            ext=extension,
            direct_url=link,
            filename=name,
            headers={'Referer': final_url},
            index=len(items) + 1,
        ))
        items[-1].group = group

    items.sort(key=lambda item: (order.get(getattr(item, 'group', 'file'), 9), item.title))
    for index, item in enumerate(items, 1):
        item.index = index

    if not items:
        result.error = 'No videos, photos or files were found on that page'
        return result
    result.items = items
    result.thumbnail = next((i.thumbnail for i in items if i.thumbnail), '')
    counts = {}
    for item in items:
        group = getattr(item, 'group', 'file')
        counts[group] = counts.get(group, 0) + 1
    result.description = ', '.join(f'{count} {group}' for group, count in counts.items())
    return result
