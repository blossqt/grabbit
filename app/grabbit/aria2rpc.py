"""Runs aria2c.exe in the background and talks to it over JSON-RPC.

aria2 is started on a free loopback port with a random secret, so nothing else
on the machine can drive it, and with --stop-with-process so it can never be
left running if Grabbit dies.
"""

import http.client
import json
import logging
import os
import secrets
import socket
import subprocess
import threading
import time
from pathlib import Path

from .util import CREATE_NO_WINDOW

log = logging.getLogger(__name__)


class Aria2Error(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code
        self.message = message


class Aria2Unavailable(Aria2Error):
    """The RPC endpoint could not be reached at all."""


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class Aria2Client:
    """Minimal JSON-RPC client. Safe to call from several threads."""

    def __init__(self, port: int, secret: str, timeout: float = 20.0):
        self.port = port
        self.token = f'token:{secret}'
        self.timeout = timeout
        self._local = threading.local()
        self._id = 0
        self._id_lock = threading.Lock()

    def _next_id(self) -> str:
        with self._id_lock:
            self._id += 1
            return str(self._id)

    def _connection(self) -> http.client.HTTPConnection:
        conn = getattr(self._local, 'conn', None)
        if conn is None:
            conn = http.client.HTTPConnection('127.0.0.1', self.port, timeout=self.timeout)
            self._local.conn = conn
        return conn

    def _drop_connection(self):
        conn = getattr(self._local, 'conn', None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None

    def _post(self, payload: str) -> dict | list:
        last_error = None
        for attempt in range(2):  # one retry: keep-alive connections do get closed
            try:
                conn = self._connection()
                conn.request('POST', '/jsonrpc', payload.encode('utf-8'),
                             {'Content-Type': 'application/json'})
                response = conn.getresponse()
                body = response.read()
                if response.status != 200:
                    raise Aria2Error(f'HTTP {response.status} from aria2')
                return json.loads(body)
            except Aria2Error:
                raise
            except (OSError, http.client.HTTPException, ValueError) as exc:
                last_error = exc
                self._drop_connection()
                if attempt == 0:
                    time.sleep(0.05)
        raise Aria2Unavailable(f'aria2 RPC unreachable: {last_error}')

    def call(self, method: str, *params):
        payload = json.dumps({
            'jsonrpc': '2.0', 'id': self._next_id(),
            'method': method, 'params': [self.token, *params],
        })
        result = self._post(payload)
        if isinstance(result, dict) and result.get('error'):
            error = result['error']
            raise Aria2Error(error.get('message', 'unknown error'), error.get('code'))
        return result.get('result') if isinstance(result, dict) else result

    def multicall(self, calls: list[tuple]) -> list:
        """Run several methods in one request.

        Returns one entry per call: the result, or an Aria2Error instance.
        """
        if not calls:
            return []
        payload = json.dumps({
            'jsonrpc': '2.0', 'id': self._next_id(), 'method': 'system.multicall',
            'params': [[{'methodName': name, 'params': [self.token, *args]} for name, args in calls]],
        })
        response = self._post(payload)
        if isinstance(response, dict) and response.get('error'):
            error = response['error']
            raise Aria2Error(error.get('message', 'unknown error'), error.get('code'))
        results = []
        for item in (response or {}).get('result') or []:
            # multicall wraps successes in a 1-element list and faults in a dict
            if isinstance(item, list):
                results.append(item[0] if item else None)
            elif isinstance(item, dict) and ('faultString' in item or 'error' in item):
                error = item.get('error') or {}
                results.append(Aria2Error(item.get('faultString') or error.get('message', 'error'),
                                          item.get('faultCode') or error.get('code')))
            else:
                results.append(item)
        return results

    def close(self):
        self._drop_connection()


class Aria2Process:
    """Owns the aria2c.exe child process."""

    def __init__(self, exe: str, options: dict[str, str], log_path: Path | None = None):
        self.exe = exe
        self.options = options
        self.log_path = log_path
        self.process: subprocess.Popen | None = None
        self.client: Aria2Client | None = None
        self.port = 0
        self.version = ''

    def start(self, attempts: int = 3) -> Aria2Client:
        last_error = None
        for _ in range(attempts):
            try:
                return self._start_once()
            except Aria2Error as exc:
                last_error = exc
                self.stop()
                time.sleep(0.3)
        raise last_error or Aria2Error('could not start aria2')

    def _start_once(self) -> Aria2Client:
        self.port = _free_port()
        secret = secrets.token_hex(16)
        args = [
            self.exe,
            '--no-conf=true',
            '--enable-rpc=true',
            '--rpc-listen-all=false',
            f'--rpc-listen-port={self.port}',
            f'--rpc-secret={secret}',
            '--rpc-max-request-size=64M',
            '--rpc-allow-origin-all=false',
            f'--stop-with-process={os.getpid()}',
            '--summary-interval=0',
            '--console-log-level=warn',
            '--download-result=hide',
            '--quiet=true',
        ]
        args += [f'--{key}={value}' for key, value in self.options.items()]
        if self.log_path:
            args += [f'--log={self.log_path}', '--log-level=warn']

        creationflags = CREATE_NO_WINDOW if os.name == 'nt' else 0
        self.process = subprocess.Popen(
            args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=creationflags, text=True, errors='replace')

        client = Aria2Client(self.port, secret)
        deadline = time.time() + 15
        while time.time() < deadline:
            if self.process.poll() is not None:
                output = ''
                try:
                    output = (self.process.stdout.read() or '').strip()[-500:]
                except Exception:
                    pass
                raise Aria2Error(f'aria2 exited immediately (code {self.process.returncode}): {output}')
            try:
                self.version = (client.call('aria2.getVersion') or {}).get('version', '')
                self.client = client
                log.info('aria2 %s started on port %s (pid %s)', self.version, self.port, self.process.pid)
                self._drain_output()
                return client
            except Aria2Error:
                time.sleep(0.1)
        raise Aria2Error('aria2 did not answer on the RPC port')

    def _drain_output(self):
        """Keep the stdout pipe empty so aria2 never blocks on a full buffer."""
        def reader(stream):
            try:
                for line in stream:
                    line = line.strip()
                    if line:
                        log.warning('aria2: %s', line[:400])
            except Exception:
                pass

        threading.Thread(target=reader, args=(self.process.stdout,), daemon=True).start()

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self, timeout: float = 6.0, wait: bool = True):
        """Ask aria2 to exit.

        aria2 answers a shutdown request and then schedules the halt about
        three seconds later, so waiting for it just freezes the window. With
        wait=False we ask and walk away: aria2 finishes saving its control
        files on its own, and --stop-with-process guarantees it cannot outlive
        Grabbit even if it gets stuck.
        """
        if self.client and self.is_running():
            try:
                self.client.call('aria2.shutdown')
            except Aria2Error:
                pass
            if not wait:
                self.client.close()
                self.client = None
                return
            deadline = time.time() + timeout
            while time.time() < deadline and self.is_running():
                time.sleep(0.1)
            if self.is_running():
                try:
                    self.client.call('aria2.forceShutdown')
                except Aria2Error:
                    pass
                deadline = time.time() + 3
                while time.time() < deadline and self.is_running():
                    time.sleep(0.1)
        if self.is_running():
            try:
                self.process.kill()
            except Exception:
                pass
        if self.client:
            self.client.close()
        self.process = None
