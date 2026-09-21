"""Finding out whether a newer Grabbit has been released.

Releases are published on GitHub by build/release.py, each carrying the
Windows zip and the Android APK. Both apps ask the same question of the same
place: which release is the latest, is it newer than this copy, and which of
its files is the one for this platform.

Only the question lives here. What to do about the answer - a banner on the
desktop, a chip on the phone - belongs to each front-end, and nothing in this
module downloads or installs anything.
"""

from __future__ import annotations

import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from . import APP_NAME, APP_VERSION

REPO = 'blossqt/grabbit'
RELEASES_PAGE = f'https://github.com/{REPO}/releases/latest'
# GitHub's API for the repository. Tests point this at a stand-in.
API = os.environ.get('GRABBIT_RELEASES_API', f'https://api.github.com/repos/{REPO}').rstrip('/')

# Which file a platform wants from a release, by how its name ends. These are
# the names release.py gives them.
PLATFORM_SUFFIX = {'windows': '-win64.zip', 'android': '-arm64.apk'}

# Shortly after starting, then twice a day - as Homework Hub does. Releases
# are occasional, and GitHub allows an address sixty unsigned requests an hour.
FIRST_CHECK_DELAY = 60
CHECK_INTERVAL = 12 * 3600

_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)')


class UpdateError(RuntimeError):
    """Why a check failed, in words fit to show."""


def parse_version(text) -> tuple | None:
    match = _VERSION.match(str(text or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate, current=APP_VERSION) -> bool:
    """Compared as numbers, so 1.10.0 is newer than 1.9.0."""
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


@dataclass
class Asset:
    name: str
    url: str
    size: int = 0


@dataclass
class Release:
    version: str
    tag: str
    title: str
    notes: str
    page: str
    published_at: str = ''
    assets: list = field(default_factory=list)

    def asset_for(self, platform: str) -> Asset | None:
        suffix = PLATFORM_SUFFIX.get(platform)
        return next((a for a in self.assets if suffix and a.name.endswith(suffix)), None)


@dataclass
class Check:
    """The answer to "is there a newer Grabbit for this platform?"."""

    current: str
    release: Release | None = None
    asset: Asset | None = None
    error: str = ''

    @property
    def available(self) -> bool:
        # A release with nothing for this platform - an Android-only fix, say -
        # is not an update for it.
        return (self.release is not None and self.asset is not None
                and is_newer(self.release.version, self.current))

    @property
    def latest(self) -> str:
        return self.release.version if self.release else ''


def _context() -> ssl.SSLContext:
    # A phone has no certificate store OpenSSL can find on its own, so use
    # certifi's everywhere - it is what aria2 and yt-dlp are given as well.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def _get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={
        'Accept': 'application/vnd.github+json',
        'User-Agent': f'{APP_NAME}/{APP_VERSION}',     # GitHub refuses requests without one
        'X-GitHub-Api-Version': '2022-11-28',
    })
    with urllib.request.urlopen(request, timeout=timeout, context=_context()) as response:
        return response.read()


def parse_release(data) -> Release:
    """A release, from what GitHub's API says about one."""
    try:
        tag = data['tag_name']
        numbers = parse_version(tag)
        if numbers is None:
            raise ValueError(tag)
        assets = [Asset(a['name'], a['browser_download_url'], int(a.get('size') or 0))
                  for a in data.get('assets') or []]
        return Release(version='.'.join(map(str, numbers)), tag=tag,
                       title=data.get('name') or f'{APP_NAME} {tag}',
                       notes=(data.get('body') or '').strip(),
                       page=data.get('html_url') or f'https://github.com/{REPO}/releases/tag/{tag}',
                       published_at=data.get('published_at') or '', assets=assets)
    except (KeyError, TypeError, ValueError):
        raise UpdateError('GitHub sent something that was not a release.') from None


def latest_release(timeout: float = 15) -> Release:
    """The newest published release. Raises UpdateError and nothing else."""
    try:
        data = json.loads(_get(f'{API}/releases/latest', timeout))
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError('No release has been published yet.') from None
        if error.code in (403, 429):
            raise UpdateError('GitHub is limiting how often this can be asked. '
                              'Try again in an hour.') from None
        raise UpdateError(f'GitHub answered {error.code}.') from None
    except (urllib.error.URLError, OSError):
        raise UpdateError("Couldn't reach GitHub. Check the internet connection.") from None
    except ValueError:
        raise UpdateError('GitHub sent something that was not a release.') from None
    return parse_release(data)


def check(platform: str, current: str = APP_VERSION, timeout: float = 15) -> Check:
    """Ask, for this platform. Never raises: a failed check is an answer too."""
    try:
        release = latest_release(timeout)
    except UpdateError as error:
        return Check(current, error=str(error))
    except Exception as error:             # a bug here must not take an app down
        return Check(current, error=f'The check failed: {error}')
    return Check(current, release, release.asset_for(platform))
