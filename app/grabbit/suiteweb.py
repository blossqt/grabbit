"""Pictures and pages on the web, as the suite's downloaders meet them: Grabbit (a pasted link)
and Dropshift (a picture or link dragged out of a browser).

Shared (Hanami's copy is the original; tests/check_copies.py keeps the others the same).

- **The full-size picture.** Sites show pictures smaller than they keep them, and the address
  says so: StockCake's .../<id>_large/..., Pixabay's _640.jpg, Flickr's _m.jpg, a resizing
  server's ?w=640, WordPress's -300x200.jpg. best() finds the full one:
  - candidates() turns an address into fuller ones, best first: a rule for each site that
    has its own way (SITES), then ways many sites share (_shared).
  - best() asks the server about each, and takes the first that answers with a picture. A
    shared way's answer must also be larger than the picture it started from: a file named
    banner_1200x1200.jpg is not a resized copy.
  - Only what a site shows freely: never a paid site's (Shutterstock, Getty, Adobe Stock,
    iStock) or a paid picture's (Unsplash+, Freepik Premium, Vecteezy's originals).
- **Robot checks.** Some sites check for robots before they show a page (Cloudflare's "Just a
  moment..."). A program asking for the page gets the check, never the page, and getting
  round it is not on. robot_check() tells such a reply apart, so the user hears why and what
  does work: the picture's own address, which is not checked.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36')
_CHECKERS = ('cloudflare', 'ddos-guard')
_PICTURE = re.compile(r'\.(jpe?g|png|webp|gif|avif|bmp|tiff?|heic)$', re.IGNORECASE)
_SIZING = {'w', 'width', 'h', 'height', 'size', 'resize', 'fit', 'crop', 'dpr', 'q', 'quality',
           'auto', 'cs', 'fm', 'format', 'semt', 'strip', 'scale', 'sz', 'quality_level'}


def _sub(pattern: str, full: str, flags=re.IGNORECASE):
    compiled = re.compile(pattern, flags)

    def rule(url: str) -> list[str]:
        return [compiled.sub(full, url, count=1)] if compiled.match(url) else []
    return rule


def _query(url: str, keep=(), put=None) -> str:
    """url with only the query parameters in keep, and those in put set."""
    parts = urllib.parse.urlsplit(url)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k in keep]
    query += list((put or {}).items())
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def _host(*hosts: str):
    def matches(url: str) -> bool:
        name = (urllib.parse.urlsplit(url).hostname or '').lower()
        return any(name == host or name.endswith('.' + host) for host in hosts)
    return matches


def _when(test, make):
    return lambda url: [make(url)] if test(url) else []


_unsplash = _host('images.unsplash.com')
_pexels = _host('images.pexels.com')
_burst = _host('burst.shopifycdn.com')
_freepik = _host('img.freepik.com', 'img.magnific.com')

# Each: an address -> fuller ones, best first. Checked against real pictures on 2026-10-29.
SITES = (
    # StockCake: .../d/d/f/<uuid>_large/<name>.jpg (512 px; _medium, _small) -> 1024 px.
    _sub(r'^(https?://images\.stockcake\.com/public/(?:[0-9a-f]/){3}[0-9a-f-]{36})_(?:large|medium|small)(/[^?#]+)',
         r'\1\2'),
    # Unsplash: the photo itself, without ?w=..&fit=..; not Unsplash+ (plus.unsplash.com, paid).
    _when(lambda url: _unsplash(url) and '/photo-' in url and '?' in url, lambda url: _query(url)),
    # Pexels: .../photos/<id>/<name>.jpeg?auto=compress&cs=tinysrgb&w=600 -> the original.
    _when(lambda url: _pexels(url) and '/photos/' in url and '?' in url, lambda url: _query(url)),
    # Pixabay: <name>-<id>_640.jpg (_150, _180, _340, _960_720) -> _1280, the largest given freely.
    _sub(r'^(https?://cdn\.pixabay\.com/photo/.+?)_(?:\d+_\d+|150|180|340|640|960)(\.\w+)$', r'\1_1280\2'),
    # Flickr: <id>_<secret>_m.jpg (s, q, t, n, w, z, c, or none) -> _b, 1024 px.
    _sub(r'^(https?://live\.staticflickr\.com/\d+/\d+_[0-9a-f]+)(?:_[sqtmnwzc])?(\.jpg)$', r'\1_b\2'),
    # StockSnap: img-thumbs/280h/<name>.jpg -> img-thumbs/960w, the largest it shows.
    _sub(r'^(https?://cdn\.stocksnap\.io/img-thumbs/)\d+[hw](/.+)$', r'\g<1>960w\2'),
    # Burst (Shopify's free photos): .../photos/<name>.jpg?width=1000 -> the original.
    _when(lambda url: _burst(url) and '?' in url, lambda url: _query(url)),
    # Freepik (now Magnific): its free pictures at 2,000 px rather than the 740 shown.
    _when(lambda url: _freepik(url) and re.search(r'/free-[a-z-]+/', url) is not None,
          lambda url: _query(url, put={'w': '2000'})),
    # Wikimedia: .../thumb/a/ab/<file>/330px-<file> -> .../a/ab/<file>, the file itself.
    _sub(r'^(https?://upload\.wikimedia\.org/wikipedia/[^/]+)/thumb(/[0-9a-f]/[0-9a-f]{2}/[^/]+)/[^/]+$', r'\1\2'),
    # Cloudinary: .../image/upload/w_300,c_scale/<v123/>name.jpg -> without the changes.
    _sub(r'^(https?://res\.cloudinary\.com/[^/]+/image/upload/)(?:[a-z]{1,3}_[^/]+/)+((?:v\d+/)?[^?#]+)$', r'\1\2'),
    # Reddit: preview.redd.it/<name>.jpg?width=640&... -> i.redd.it/<name>.jpg.
    _sub(r'^https?://preview\.redd\.it/([^/?#]+)(?:\?.*)?$', r'https://i.redd.it/\1'),
    # X: pbs.twimg.com/media/<id>?format=jpg&name=small (or <id>.jpg:small) -> name=orig.
    _sub(r'^(https?://pbs\.twimg\.com/media/[\w-]+)(?:\?format=(\w+)&name=\w+|\.(\w+)(?::\w+)?)$',
         lambda m: f'{m.group(1)}?format={m.group(2) or m.group(3)}&name=orig'),
    # Pinterest: i.pinimg.com/236x/aa/bb/cc/<hash>.jpg -> originals, then 736x.
    lambda url: [re.sub(r'^(https?://i\.pinimg\.com/)[^/]+/', rf'\1{size}/', url, count=1)
                 for size in ('originals', '736x')]
    if re.match(r'^https?://i\.pinimg\.com/(?!originals/)[^/]+/[0-9a-f]{2}/', url) else [],
    # Imgur: <id>m.jpg (s, t, l, h, b) -> <id>.jpg.
    _sub(r'^(https?://i\.imgur\.com/(?:[A-Za-z0-9]{5}|[A-Za-z0-9]{7}))[stmlhb](\.\w+)$', r'\1\2'),
    # Google (Blogger, Photos): ...=w400-h300 or /s320/ -> =s0 or /s0/, the original.
    _sub(r'^(https?://(?:lh\d|blogger)\.googleusercontent\.com/.+)=[\w-]+$', r'\1=s0'),
    _sub(r'^(https?://blogger\.googleusercontent\.com/img/.+?/)[swh]\d+(?:-[\w-]+)?(/[^/]+)$', r'\1s0\2'),
    # Amazon: <id>._AC_SX300_.jpg -> <id>.jpg.
    _sub(r'^(https?://m\.media-amazon\.com/images/I/[\w+%-]+)\.[^/]+?(\.\w+)$', r'\1\2'),
    # eBay: .../s-l300.jpg -> s-l1600, the largest.
    _sub(r'^(https?://i\.ebayimg\.com/.+/s-l)\d+(\.\w+)$', r'\g<1>1600\2'),
    # Etsy: il_340x270.<id>.jpg -> il_fullxfull.
    _sub(r'^(https?://i\.etsystatic\.com/.+/il_)\d+x\d+(\.[^/]+)$', r'\1fullxfull\2'),
)


def _shared(url: str) -> list[str]:
    """Ways many sites resize: a size in the name (-300x200, Shopify's _640x), or in the query
    (?w=640, ?resize=..). Only for an address of a picture."""
    parts = urllib.parse.urlsplit(url)
    if not _PICTURE.search(parts.path):
        return []
    found = []
    path = re.sub(r'-\d{2,4}x\d{2,4}(?=\.\w+$)', '', parts.path)                         # WordPress
    path = re.sub(r'_(?:\d{2,4}x\d{0,4}|x\d{2,4})(?:_crop_\w+)?(?:@\dx)?(?=\.\w+$)', '', path)   # Shopify
    if path != parts.path:
        found.append(urllib.parse.urlunsplit(parts._replace(path=path)))
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    if any(key.lower() in _SIZING for key, _ in query):
        kept = [key for key, _ in query if key.lower() not in _SIZING]
        found += [_query(address, keep=kept) for address in (found or [url])]
    return found


def candidates(url: str) -> list[tuple[str, bool]]:
    """Fuller addresses for a picture, best first, each with whether it is a site's own way
    (sure) rather than a shared guess. A Next.js /_next/image?url= address is unwrapped first:
    on a site behind a robot check it is checked too, the picture it resizes may not be."""
    inner = _unwrapped(url or '')
    found = [(address, True) for rule in SITES for address in rule(inner)]
    found += [(address, False) for address in _shared(inner)]
    if inner != url:
        found.append((inner, True))
    seen, out = {url}, []
    for address, sure in found:
        if address not in seen:
            seen.add(address)
            out.append((address, sure))
    return out


def _unwrapped(url: str) -> str:
    """The picture a resizing server serves, or url itself: Next.js's /_next/image?url=<it>,
    and WordPress's i0.wp.com/<site>/<path> (which also turns away Python's requests)."""
    parts = urllib.parse.urlsplit(url)
    if re.fullmatch(r'i[0-3]\.wp\.com', parts.hostname or ''):
        site = re.match(r'^/([a-z0-9.-]+\.[a-z]{2,})(/.+)$', parts.path, re.IGNORECASE)
        return f'https://{site.group(1)}{site.group(2)}' if site else url
    if not parts.path.endswith('/_next/image'):
        return url
    inner = urllib.parse.parse_qs(parts.query).get('url', [''])[0]
    whole = urllib.parse.urljoin(url, inner) if inner else ''
    return whole if whole.startswith(('http://', 'https://')) else url


def best(url: str, referer: str = '', timeout: float = 10) -> str:
    """The fullest picture to be had for url: the first candidate whose server answers with a
    picture (and, for a shared guess, a larger one), else url itself. Off the window's thread:
    each candidate is a request."""
    found = candidates(url)
    if not found:
        return url
    start = None
    for address, sure in found:
        kind, size = _ask(address, referer, timeout)
        if not kind.startswith('image/'):
            continue
        if sure:
            return address
        if start is None:              # what it started from: the resizing server's picture, if any
            start = _ask(_unwrapped(url), referer, timeout)[1]
        if size and start and size > start:
            return address
    return url


def _ask(url: str, referer: str, timeout: float) -> tuple[str, int]:
    """(content type, size) as the server tells them: HEAD, or the first byte where HEAD is
    turned away. ('', 0) if it can't be asked."""
    headers = {'User-Agent': USER_AGENT, 'Accept': 'image/*,*/*'}
    if referer:
        headers['Referer'] = referer
    for method in ('HEAD', 'GET'):
        extra = {'Range': 'bytes=0-0'} if method == 'GET' else {}
        try:
            request = urllib.request.Request(url, headers={**headers, **extra}, method=method)
            with urllib.request.urlopen(request, timeout=timeout) as reply:
                kind = reply.headers.get_content_type()
                size = reply.headers.get('Content-Length') or ''
                whole = (reply.headers.get('Content-Range') or '').rsplit('/', 1)[-1]
                size = whole if whole.isdigit() else size
                return kind, int(size) if size.isdigit() else 0
        except urllib.error.HTTPError as error:
            if method == 'GET' or error.code not in (403, 405, 501):
                return '', 0
        except (OSError, ValueError):
            return '', 0
    return '', 0


def robot_check(status: int, headers) -> bool:
    """Whether a reply is a site's check for robots rather than what was asked for. headers:
    anything with .get, as urllib's are."""
    if (headers.get('cf-mitigated') or '').lower() == 'challenge':
        return True
    server = (headers.get('server') or '').lower()
    return status in (403, 429, 503) and any(name in server for name in _CHECKERS)
