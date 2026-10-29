"""Pictures and pages on the web, as the suite's downloaders meet them: Grabbit (a pasted link)
and Dropshift (a picture or link dragged out of a browser).

Shared (Hanami's copy is the original; tests/check_copies.py keeps the others the same).

- Some sites show a picture smaller than they keep it, and its address says so. StockCake's
  page shows .../<id>_large/<name>.jpg (512 px, and _medium, _small); its own picture is
  .../<id>/<name>.jpg (1024 px), the page's og:image. fuller() gives that address. Callers
  take it only once it answers with a picture.
- Some sites check for robots before they show a page (Cloudflare's "Just a moment..."). A
  program asking for the page gets the check, never the page, and getting round it is not
  on. robot_check() tells such a reply apart, so the user hears why and what does work: the
  picture's own address, which is not checked.
"""

from __future__ import annotations

import re

FULLER = (
    # images.stockcake.com/public/d/d/f/<uuid>_large/<name>.jpg -> .../<uuid>/<name>.jpg
    (re.compile(r'^(https?://images\.stockcake\.com/public/(?:[0-9a-f]/){3}[0-9a-f-]{36})'
                r'_(?:large|medium|small)(/[^?#]+)', re.IGNORECASE), r'\1\2'),
)

_CHECKERS = ('cloudflare', 'ddos-guard')


def fuller(url: str) -> str:
    """The full-size picture behind a known site's smaller copy, or '' if there is none."""
    for pattern, full in FULLER:
        if pattern.match(url or ''):
            return pattern.sub(full, url, count=1)
    return ''


def robot_check(status: int, headers) -> bool:
    """Whether a reply is a site's check for robots rather than what was asked for. headers:
    anything with .get, as urllib's are."""
    if (headers.get('cf-mitigated') or '').lower() == 'challenge':
        return True
    server = (headers.get('server') or '').lower()
    return status in (403, 429, 503) and any(name in server for name in _CHECKERS)
