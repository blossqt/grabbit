"""What a release is, and how an app decides to believe one.

Releases are published on GitHub by build/release.py. Besides the Windows zip
and the Android APK, each one carries:

    latest.json        which version this is, and each file's size and SHA-256
    latest.json.sig    an Ed25519 signature over latest.json's exact bytes

Both apps fetch the newest manifest from GitHub's stable "latest release"
address, and believe it only when the signature checks out against the public
key built into the app (release_key.py). The key that signs never leaves the
machine that publishes, so even someone who got into the GitHub account could
not ship an update Grabbit would install. The phone has a second line of
defence of its own: Android refuses an update not signed like the installed app.

This is Homework Hub's arrangement, file for file, so the two are published
and trusted the same way. Installing is not in here: selfupdate.py does that on
Windows, and the phone hands the APK to Android's installer.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import APP_NAME, APP_VERSION, ed25519
from .release_key import PUBLIC_KEY

REPO = 'blossqt/grabbit'
RELEASES_PAGE = f'https://github.com/{REPO}/releases/latest'
MANIFEST_NAME = 'latest.json'
SIGNATURE_NAME = 'latest.json.sig'
SCHEMA = 1

# Both apps check the moment they are on screen, and then twice a day.
CHECK_INTERVAL = 12 * 3600

PLATFORMS = ('windows', 'android')

_VERSION = re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')
_HEX32 = re.compile(r'^[0-9a-f]{64}$')
_FILE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')

# For build/selfupdate_check.py: a stand-in site, the key it signs with, and a
# version to pretend to be. All three are ignored unless the site is set, so
# nothing a user does by accident can point Grabbit anywhere but GitHub.
ENV_SITE, ENV_KEY, ENV_CURRENT = 'GRABBIT_UPDATE_SITE', 'GRABBIT_UPDATE_KEY', 'GRABBIT_UPDATE_CURRENT'


class UpdateError(RuntimeError):
    """Why a check or an install cannot go ahead, in words fit to show."""


def parse_version(text) -> tuple | None:
    match = _VERSION.match(str(text or '').strip())
    return tuple(int(part) for part in match.groups()) if match else None


def is_newer(candidate, current=APP_VERSION) -> bool:
    """Compared as numbers, so 1.10.0 is newer than 1.9.0."""
    a, b = parse_version(candidate), parse_version(current)
    return a is not None and b is not None and a > b


def _test_site() -> str:
    return os.environ.get(ENV_SITE, '').strip().rstrip('/')


def site() -> str:
    return _test_site() or f'https://github.com/{REPO}'


def public_key() -> str:
    override = os.environ.get(ENV_KEY, '').strip().lower()
    if _test_site() and _HEX32.match(override):
        return override
    return PUBLIC_KEY


def current_version() -> str:
    override = os.environ.get(ENV_CURRENT, '').strip()
    if _test_site() and parse_version(override):
        return override
    return APP_VERSION


@dataclass(frozen=True)
class Asset:
    name: str
    size: int
    sha256: str
    url: str


@dataclass(frozen=True)
class Release:
    version: str
    published_at: str
    notes: str
    desktop: Asset | None
    android: Asset | None

    @property
    def page(self) -> str:
        return f'https://github.com/{REPO}/releases/tag/v{self.version}'

    def asset_for(self, platform: str) -> Asset | None:
        return self.desktop if platform == 'windows' else self.android


# ------------------------------------------------------------------ publishing

def file_digest(path: Path) -> tuple:
    digest, size = hashlib.sha256(), 0
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
            size += len(chunk)
    return size, digest.hexdigest()


def build_manifest(version: str, *, notes: str, published_at: str,
                   desktop: Path | None, android: Path | None) -> bytes:
    """The bytes release.py signs and uploads."""
    if parse_version(version) is None:
        raise ValueError(f'not a version: {version!r}')
    body = {'schema': SCHEMA, 'app': APP_NAME, 'version': version,
            'published_at': published_at, 'notes': notes, 'desktop': None, 'android': None}
    for key, path in (('desktop', desktop), ('android', android)):
        if path is not None:
            size, sha = file_digest(path)
            body[key] = {'file': Path(path).name, 'size': size, 'sha256': sha}
    return (json.dumps(body, indent=2) + '\n').encode('utf-8')


def sign_manifest(manifest: bytes, secret_hex: str) -> str:
    return base64.b64encode(ed25519.sign(bytes.fromhex(secret_hex), manifest)).decode('ascii')


# ------------------------------------------------------------------- believing

def _asset(entry, version: str, label: str) -> Asset | None:
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise UpdateError(f'The {label} entry in the update is malformed.')
    name = str(entry.get('file') or '')
    sha = str(entry.get('sha256') or '').lower()
    try:
        size = int(entry.get('size'))
    except (TypeError, ValueError):
        size = -1
    if not _FILE.match(name) or not _HEX32.match(sha) or size <= 0:
        raise UpdateError(f'The {label} entry in the update is missing its file, size or hash.')
    return Asset(name, size, sha, f'{site()}/releases/download/v{version}/{name}')


def verify_and_parse(manifest: bytes, signature, key: str | None = None) -> Release:
    """Check the signature first, and only then read a word of the contents."""
    key = public_key() if key is None else key
    if not _HEX32.match(key or ''):
        raise UpdateError('Updates are not set up for this build.')
    try:
        raw = base64.b64decode(signature.strip() if isinstance(signature, (bytes, str)) else b'',
                               validate=True)
    except (ValueError, TypeError):
        raise UpdateError('The update’s signature is unreadable.') from None
    if not ed25519.verify(bytes.fromhex(key), manifest, raw):
        raise UpdateError('The update’s signature does not match, so it cannot be trusted.')

    try:
        body = json.loads(manifest.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise UpdateError('The update description is unreadable.') from None
    if not isinstance(body, dict) or body.get('schema') != SCHEMA or body.get('app') != APP_NAME:
        raise UpdateError('The update is for a different app, or a newer format.')
    version = str(body.get('version') or '')
    if parse_version(version) is None:
        raise UpdateError('The update has no usable version.')
    return Release(version=version,
                   published_at=str(body.get('published_at') or ''),
                   notes=str(body.get('notes') or '')[:4000],
                   desktop=_asset(body.get('desktop'), version, 'Windows'),
                   android=_asset(body.get('android'), version, 'Android'))


# ------------------------------------------------------------------- fetching

def ssl_context() -> ssl.SSLContext:
    # A phone has no certificate store OpenSSL can find on its own, so use
    # certifi's everywhere - it is what aria2 and yt-dlp are given as well.
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


def open_url(url: str, timeout: float):
    request = urllib.request.Request(url, headers={'User-Agent': f'{APP_NAME}/{APP_VERSION}'})
    context = ssl_context() if url.startswith('https:') else None
    return urllib.request.urlopen(request, timeout=timeout, context=context)


def _fetch(url: str, timeout: float) -> bytes:
    with open_url(url, timeout) as response:
        return response.read(1 << 20)          # a manifest is a few hundred bytes


def latest_release(timeout: float = 15) -> Release:
    """The newest release, verified. Raises UpdateError and nothing else."""
    base = f'{site()}/releases/latest/download'
    try:
        manifest = _fetch(f'{base}/{MANIFEST_NAME}', timeout)
        signature = _fetch(f'{base}/{SIGNATURE_NAME}', timeout)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            raise UpdateError('No update information has been published yet.') from None
        if error.code in (403, 429):
            raise UpdateError('GitHub is limiting how often this can be asked. '
                              'Try again in an hour.') from None
        raise UpdateError(f'GitHub answered {error.code}.') from None
    except (urllib.error.URLError, OSError):
        raise UpdateError("Couldn't reach GitHub. Check the internet connection.") from None
    return verify_and_parse(manifest, signature)


def download(asset: Asset, folder: Path, progress=None, cancel=None) -> Path:
    """Fetch a release file and prove it is the one the signed manifest
    describes - size and SHA-256 - before anything opens it. Both apps use
    this: the zip on Windows, the APK on a phone."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / asset.name
    if final.exists() and file_digest(final) == (asset.size, asset.sha256):
        return final
    part = final.with_name(final.name + '.part')
    digest, done = hashlib.sha256(), 0
    try:
        with open_url(asset.url, timeout=60) as response, part.open('wb') as out:
            for chunk in iter(lambda: response.read(1 << 16), b''):
                if cancel is not None and cancel.is_set():
                    raise UpdateError('The update was cancelled.')
                done += len(chunk)
                if done > asset.size:
                    raise UpdateError('The download is bigger than the release says.')
                out.write(chunk)
                digest.update(chunk)
                if progress:
                    progress(done, asset.size)
        if done != asset.size or digest.hexdigest() != asset.sha256:
            raise UpdateError('The download didn’t match the release, so it wasn’t installed.')
    except UpdateError:
        part.unlink(missing_ok=True)
        raise
    except OSError as error:
        part.unlink(missing_ok=True)
        raise UpdateError(f'The download failed: {error}') from None
    os.replace(part, final)
    return final


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


def check(platform: str, current: str | None = None, timeout: float = 15) -> Check:
    """Ask, for this platform. Never raises: a failed check is an answer too."""
    current = current or current_version()
    try:
        release = latest_release(timeout)
    except UpdateError as error:
        return Check(current, error=str(error))
    except Exception as error:             # a bug here must not take an app down
        return Check(current, error=f'The check failed: {error}')
    return Check(current, release, release.asset_for(platform))
