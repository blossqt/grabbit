"""Checks how the apps decide to believe an update - against a stand-in for
GitHub, and then the real thing.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\update_check.py

An app that installs its own updates must refuse anything not signed with the
release key, so most of this is about refusing: a manifest signed with another
key, one changed after it was signed, one for another app, none at all. The
Ed25519 code is checked against RFC 8032's own test vectors first, since every
other answer rests on it.
"""

import os
import sys
import threading
from functools import partial
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))

from grabbit import ed25519, updates  # noqa: E402

results = []


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''))


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def check_ed25519():
    print('signatures (RFC 8032, section 7.1)')
    vectors = [
        ('9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60',
         'd75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a', '',
         'e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e3970'
         '1cf9b46bd25bf5f0595bbe24655141438e7a100b'),
        ('4ccd089b28ff96da9db6c346ec114e0f5b8a319f35aba624da8cf6ed4fb8a6fb',
         '3d4017c3e843895a92b70aa74d1b7ebc9c982ccf2ec4968cc0cd55f12af4660c', '72',
         '92a009a9f0d4cab8720e820b5f642540a2b27b5416503f8fb3762223ebdb69da085ac1e43e15996e458f3613'
         'd0f11d8c387b2eaeb4302aeeb00d291612bb0c00')]
    for number, (secret, public, message, signature) in enumerate(vectors, 1):
        s, m = bytes.fromhex(secret), bytes.fromhex(message)
        report(f'test {number}: key, signature and verification match',
               ed25519.public_key(s).hex() == public and ed25519.sign(s, m).hex() == signature
               and ed25519.verify(bytes.fromhex(public), m, bytes.fromhex(signature)))
    report('a changed message is refused', not ed25519.verify(
        bytes.fromhex(vectors[1][1]), b'\x73', bytes.fromhex(vectors[1][3])))


def main():
    check_ed25519()
    report('this build carries a release key', len(updates.PUBLIC_KEY) == 64,
           updates.PUBLIC_KEY[:16] + '…' if updates.PUBLIC_KEY else 'none')

    print('\nversions')
    report('1.10.0 is newer than 1.9.0', updates.is_newer('1.10.0', '1.9.0'))
    report('a tag reads as its version', updates.parse_version('v1.2.0') == (1, 2, 0))
    report('nonsense is never newer', not updates.is_newer('banana', '1.2.0'))

    # A stand-in for github.com, laid out the way release downloads are.
    site = Path(tempfile.mkdtemp(prefix='grabbit-update-check-'))
    latest = site / 'releases' / 'latest' / 'download'
    latest.mkdir(parents=True)
    server = HTTPServer(('127.0.0.1', 0), partial(Quiet, directory=str(site)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    secret = ed25519.generate_secret()
    os.environ[updates.ENV_SITE] = f'http://127.0.0.1:{server.server_port}'
    os.environ[updates.ENV_KEY] = ed25519.public_key(secret).hex()

    zip_file, apk_file = site / 'Grabbit-9.0.0-win64.zip', site / 'Grabbit-9.0.0-arm64.apk'
    zip_file.write_bytes(b'zip' * 1000)
    apk_file.write_bytes(b'apk' * 1000)

    def publish(version='9.0.0', desktop=zip_file, android=apk_file, key=secret, tamper=None):
        manifest = updates.build_manifest(version, notes='What changed.', published_at='now',
                                          desktop=desktop, android=android)
        signature = updates.sign_manifest(manifest, key.hex())
        if tamper:
            manifest = tamper(manifest)
        (latest / updates.MANIFEST_NAME).write_bytes(manifest)
        (latest / updates.SIGNATURE_NAME).write_text(signature)

    print('\nagainst a stand-in for GitHub')
    publish()
    for platform, asset in (('windows', zip_file), ('android', apk_file)):
        answer = updates.check(platform, current='1.2.0')
        size, sha = updates.file_digest(asset)
        report(f'a newer signed release is offered on {platform}',
               answer.available and answer.asset.name == asset.name
               and answer.asset.sha256 == sha and answer.asset.size == size,
               answer.asset.url if answer.asset else answer.error)
    report('it carries what the question shows', answer.release.notes == 'What changed.'
           and answer.release.page.endswith('/releases/tag/v9.0.0'))

    publish('1.2.0')
    answer = updates.check('windows', current='1.2.0')
    report('the running version is not offered', not answer.available and not answer.error,
           f'latest {answer.latest}')
    publish(android=apk_file, desktop=None)
    report('a release with nothing for Windows is not offered there',
           not updates.check('windows', current='1.2.0').available)

    print('\nwhat must be refused')
    stranger = ed25519.generate_secret()
    for what, kwargs, expect in (
            ('a manifest signed with another key', {'key': stranger}, 'does not match'),
            ('a manifest changed after signing',
             {'tamper': lambda m: m.replace(b'"9.0.0"', b'"9.9.9"')}, 'does not match')):
        publish(**kwargs)
        answer = updates.check('windows', current='1.2.0')
        report(f'{what} is refused', not answer.available and expect in answer.error, answer.error)

    other = updates.build_manifest('9.0.0', notes='', published_at='now', desktop=zip_file,
                                   android=None).replace(b'"Grabbit"', b'"HomeworkHub"')
    (latest / updates.MANIFEST_NAME).write_bytes(other)
    (latest / updates.SIGNATURE_NAME).write_text(updates.sign_manifest(other, secret.hex()))
    answer = updates.check('windows', current='1.2.0')
    report("another app's release is refused, even signed", not answer.available
           and 'different app' in answer.error, answer.error)

    (latest / updates.SIGNATURE_NAME).write_text('not base64 at all!')
    answer = updates.check('windows', current='1.2.0')
    report('an unreadable signature is refused', 'unreadable' in answer.error, answer.error)

    (latest / updates.MANIFEST_NAME).unlink()
    answer = updates.check('windows', current='1.2.0')
    report('no manifest at all is an answer, not a crash', 'No update information' in answer.error,
           answer.error)

    server.shutdown()
    server.server_close()
    answer = updates.check('windows', current='1.2.0', timeout=3)
    report('no answer at all is one too', 'reach GitHub' in answer.error, answer.error)

    print('\nthe real repository (needs the internet)')
    del os.environ[updates.ENV_SITE], os.environ[updates.ENV_KEY]
    live = updates.check('windows', current='0.0.1')
    if live.release is not None:
        report('GitHub serves a release this build believes', live.available,
               f'{live.latest}: {live.asset.name if live.asset else "no zip"}')
    elif 'No update information' in live.error:
        print('  [INFO] nothing signed has been published yet - the first release made with '
              'release.ps1 will be')
    elif 'reach GitHub' in live.error:
        print(f'  [SKIP] could not ask GitHub - {live.error}')
    else:
        report('GitHub serves a release this build believes', False, live.error)

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
