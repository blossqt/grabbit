"""Talks to the downloader on a plugged-in phone, as the window does.

Run inside the app through run-as (background_test.ps1 does), it reads where
the downloader listens, gives its secret, and asks. The run-as process has no
DNS of its own, but it needs none: it only reaches 127.0.0.1, and the
downloader - an ordinary app process - does the fetching.

    python device_client.py status
    python device_client.py add <url>
    python device_client.py remove <name-part>
"""

import os
import socket
import sys

APP = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP)
sys.path.insert(0, os.path.join(APP, 'shared'))

from grabbit import analyze as analyze_mod     # noqa: E402
from grabbit_mobile import wire                # noqa: E402


def connect():
    found = wire.published()
    if not found:
        sys.exit('no downloader is running (no engine.json)')
    sock = socket.create_connection(('127.0.0.1', int(found['port'])), timeout=10)
    wire.send(sock, {'secret': found['secret']})
    reader = wire.Reader(sock)
    hello = reader.read()
    if not hello or not hello.get('ok'):
        sys.exit('the downloader did not accept the secret')
    return sock, reader


def ask(sock, reader, request):
    request = dict(request, id=1)
    wire.send(sock, request)
    reply = reader.read()
    if not reply or not reply.get('ok'):
        sys.exit(f'refused: {reply}')
    return reply.get('result')


def main():
    command = sys.argv[1] if len(sys.argv) > 1 else 'status'
    sock, reader = connect()
    if command == 'add':
        url = sys.argv[2]
        name = url.rsplit('/', 1)[-1]
        link = analyze_mod.Analysis(url=url, kind=analyze_mod.KIND_FILE, title=name, filename=name)
        if url.startswith('magnet:'):
            link = analyze_mod.Analysis(url=url, kind=analyze_mod.KIND_MAGNET, title=name)
        ask(sock, reader, {'op': 'add', 'analysis': wire.encode(link), 'choice': {}})
        print(f'asked for {url}')
    elif command == 'remove':
        snapshot = ask(sock, reader, {'op': 'snapshot'})
        ids = [t['id'] for t in snapshot['tasks'] if sys.argv[2] in (t['name'] or '')]
        ask(sock, reader, {'op': 'remove', 'ids': ids, 'delete_files': True})
        print(f'removed {len(ids)}')
    snapshot = ask(sock, reader, {'op': 'snapshot'})
    print(f"downloader {snapshot['boot']} pid {os.getppid()} running={snapshot['running']} "
          f"aria2 {snapshot['version']} held={snapshot['held']} stats={snapshot['stats']}")
    for task in snapshot['tasks']:
        print(f"  {task['state']:<11} {task['done']:>12} / {task['total']:<12} "
              f"{task['down_speed']:>10}/s  {(task['name'] or task['source'])[:50]}  "
              f"{task['progress_note'] or task['error']}")


if __name__ == '__main__':
    main()
