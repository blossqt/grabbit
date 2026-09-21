"""Checks the update checker, against a stand-in for GitHub and the real thing.

    %LOCALAPPDATA%\\GrabbitBuild\\venv\\Scripts\\python.exe build\\update_check.py

Both apps decide whether to offer an update from grabbit.updates alone, so this
walks it through every answer GitHub can give - a newer release, the same one,
one with nothing for this platform, none at all, a refusal, nonsense, silence -
using a local server that speaks the same API. Then it asks the real
repository once, which needs the internet and is reported separately.
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app'))

from grabbit import updates  # noqa: E402

results = []


def report(name, ok, detail=''):
    results.append((name, ok))
    print(f'  [{"PASS" if ok else "FAIL"}] {name}' + (f' - {detail}' if detail else ''))


def release(tag, *names):
    return {
        'tag_name': tag, 'name': f'Grabbit {tag[1:]}', 'body': 'What changed.',
        'html_url': f'https://github.com/blossqt/grabbit/releases/tag/{tag}',
        'published_at': '2026-09-21T00:00:00Z',
        'assets': [{'name': n, 'browser_download_url': f'https://example.invalid/{n}', 'size': 1234}
                   for n in names],
    }


class StandIn(BaseHTTPRequestHandler):
    """Answers GET /releases/latest with whatever the test has set."""
    answer = (200, b'{}')

    def do_GET(self):
        status, body = StandIn.answer
        if self.path != '/releases/latest':
            status, body = 404, b'{"message": "Not Found"}'
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(status, payload):
    StandIn.answer = (status, payload if isinstance(payload, bytes) else json.dumps(payload).encode())


def main():
    print('versions')
    report('1.10.0 is newer than 1.9.0', updates.is_newer('1.10.0', '1.9.0'))
    report('a tag reads as its version', updates.parse_version('v1.2.0') == (1, 2, 0))
    report('the same version is not newer', not updates.is_newer('v1.2.0', '1.2.0'))
    report('nonsense is never newer', not updates.is_newer('banana', '1.2.0'))

    server = HTTPServer(('127.0.0.1', 0), StandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    updates.API = f'http://127.0.0.1:{server.server_port}'

    print('\nagainst a stand-in for GitHub')
    both = release('v9.0.0', 'Grabbit-9.0.0-win64.zip', 'Grabbit-9.0.0-win64.zip.sha256',
                   'Grabbit-9.0.0-arm64.apk', 'Grabbit-9.0.0-arm64.apk.sha256')
    serve(200, both)
    for platform, suffix in (('windows', '.zip'), ('android', '.apk')):
        answer = updates.check(platform, current='1.2.0')
        report(f'a newer release is offered on {platform}',
               answer.available and answer.asset.name.endswith(suffix) and not answer.error,
               answer.asset.name if answer.asset else answer.error)
    report('it carries what the release page needs',
           answer.release.page.endswith('/v9.0.0') and answer.release.notes == 'What changed.',
           answer.release.page)

    serve(200, release('v1.2.0', 'Grabbit-1.2.0-win64.zip'))
    answer = updates.check('windows', current='1.2.0')
    report('the running version is not offered', not answer.available and not answer.error,
           f'latest {answer.latest}')

    serve(200, release('v1.1.0', 'Grabbit-1.1.0-win64.zip'))
    report('an older one is not offered', not updates.check('windows', current='1.2.0').available)

    serve(200, release('v9.0.0', 'Grabbit-9.0.0-arm64.apk'))
    report('a release with nothing for Windows is not offered there',
           not updates.check('windows', current='1.2.0').available)
    report('... but is on the phone', updates.check('android', current='1.2.0').available)

    for status, payload, expect, what in (
            (404, {'message': 'Not Found'}, 'No release', 'no release at all'),
            (403, {'message': 'API rate limit exceeded'}, 'limiting', 'being rate-limited'),
            (200, b'<html>not json</html>', 'not a release', 'nonsense'),
            (200, {'tag_name': 'nightly'}, 'not a release', 'a tag that is not a version')):
        serve(status, payload)
        answer = updates.check('windows', current='1.2.0')
        report(f'{what} is an answer, not a crash', not answer.available and expect in answer.error,
               answer.error)

    server.shutdown()
    server.server_close()
    answer = updates.check('windows', current='1.2.0', timeout=3)
    report('no answer at all is one too', not answer.available and 'reach GitHub' in answer.error,
           answer.error)

    print('\nthe real repository (needs the internet)')
    updates.API = f'https://api.github.com/repos/{updates.REPO}'
    live = updates.check('windows', current='0.0.1')
    if live.error:
        print(f'  [SKIP] could not ask GitHub - {live.error}')
    else:
        report('GitHub has a latest release with a Windows zip', live.available,
               f'{live.release.tag}: {live.asset.name if live.asset else "no zip"}')

    failures = [name for name, ok in results if not ok]
    print(f'\n{len(results) - len(failures)}/{len(results)} checks passed')
    for name in failures:
        print(f'  failed: {name}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
