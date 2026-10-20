"""suitelink: one suite app's line to Hanami, which keeps the suite's one tray icon.

SHARED FILE - the same file sits in every suite app (Keytap, Dropshift,
Grabbit, Homework Hub). This copy, in Hanami, is the original: change it here
and copy it over; Hanami's tests/check_copies.py says whether they match.

Standard library only (ctypes for the pipe), so an app needs no Qt to use it.

An app makes a SuiteLink and keeps telling it its tray menu. While Hanami is
running, the menu shows as a submenu of Hanami's cherry-blossom icon, clicks
come back through on_click, and the app hides its own tray icon (on_state
says when). When Hanami is not running - or quits - on_state(False) tells the
app to show its own icon again. The link keeps trying to reach Hanami in the
background, so starting Hanami later folds the app in.

The menu is a list of dicts:
    {'id': 'lock', 'text': 'Lock'}                  a command
    {'id': 'pause', 'text': 'Pause', 'checkable': True, 'checked': False}
    {'text': 'Locked', 'enabled': False}           a line of status
    {'separator': True}
    'default': True on one item makes it bold (what clicking the app's icon does)

Callbacks run on the link's own thread; a Qt app should hand them to its UI
thread (a signal does that).

Wire format: one JSON object per line over the named pipe
\\\\.\\pipe\\hanami-tray-<user>.
    app -> Hanami   {"t": "hello", "app", "pid", "icon": base64 PNG}
                    {"t": "menu", "items": [...], "tooltip"}
                    {"t": "notify", "title", "body"}
    Hanami -> app   {"t": "click", "id"}
"""

from __future__ import annotations

import base64
import ctypes
import json
import os
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

# HANAMI_PIPE lets tests run a Hanami of their own beside the real one.
PIPE_NAME = os.environ.get('HANAMI_PIPE') or f'hanami-tray-{os.environ.get("USERNAME", "user")}'
PIPE_PATH = '\\\\.\\pipe\\' + PIPE_NAME
VERSION = 1

_kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
_kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_kernel32.CreateFileW.restype = wintypes.HANDLE
_kernel32.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                    ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel32.PeekNamedPipe.restype = wintypes.BOOL
_kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                               ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel32.ReadFile.restype = wintypes.BOOL
_kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
_kernel32.WriteFile.restype = wintypes.BOOL
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.CloseHandle.restype = wintypes.BOOL

_GENERIC_READ, _GENERIC_WRITE = 0x80000000, 0x40000000
_OPEN_EXISTING = 3
_INVALID_HANDLE = wintypes.HANDLE(-1).value


class SuiteLink:
    def __init__(self, app: str, icon_png: bytes = b'', on_click: Callable[[str], None] | None = None,
                 on_state: Callable[[bool], None] | None = None, poll: float = 0.1, retry: float = 2.0):
        self.app = app
        self._icon = base64.b64encode(icon_png).decode('ascii') if icon_png else ''
        self._on_click = on_click or (lambda _id: None)
        self._on_state = on_state or (lambda _up: None)
        self._poll, self._retry = poll, retry
        self._menu: dict | None = None
        self._outbox: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.connected = False

    # ------------------------------------------------------------------ the app's side

    def start(self) -> None:
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name=f'suitelink-{self.app}', daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """Leave Hanami's menu (on quit, or when the app hides from the tray)."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        self._stop.clear()

    def set_menu(self, items: list[dict], tooltip: str = '') -> None:
        message = {'t': 'menu', 'items': items, 'tooltip': tooltip or self.app}
        with self._lock:
            if message == self._menu:
                return
            self._menu = message
            if self.connected:
                self._outbox.append(json.dumps(message))

    def notify(self, title: str, body: str = '') -> bool:
        """Show a notification from Hanami's icon; False if Hanami isn't there to show it."""
        with self._lock:
            if not self.connected:
                return False
            self._outbox.append(json.dumps({'t': 'notify', 'title': title, 'body': body}))
            return True

    # ------------------------------------------------------------------ the link's thread

    def _run(self) -> None:
        while not self._stop.is_set():
            handle = _kernel32.CreateFileW(PIPE_PATH, _GENERIC_READ | _GENERIC_WRITE, 0, None,
                                           _OPEN_EXISTING, 0, None)
            if not handle or handle == _INVALID_HANDLE:
                self._stop.wait(self._retry)            # Hanami isn't running (yet)
                continue
            try:
                self._session(handle)
            finally:
                _kernel32.CloseHandle(handle)
                if self.connected:
                    with self._lock:
                        self.connected = False
                    self._call(self._on_state, False)
            self._stop.wait(self._retry)

    def _session(self, handle) -> None:
        hello = {'t': 'hello', 'app': self.app, 'pid': os.getpid(), 'icon': self._icon, 'v': VERSION}
        with self._lock:
            self._outbox = [json.dumps(hello)] + ([json.dumps(self._menu)] if self._menu else [])
            self.connected = True
        self._call(self._on_state, True)
        buffer = b''
        while not self._stop.is_set():
            with self._lock:
                outgoing, self._outbox = self._outbox, []
            for line in outgoing:
                if not _write(handle, (line + '\n').encode('utf-8')):
                    return
            waiting = wintypes.DWORD()
            if not _kernel32.PeekNamedPipe(handle, None, 0, None, ctypes.byref(waiting), None):
                return                                  # Hanami went away
            if waiting.value:
                chunk = ctypes.create_string_buffer(waiting.value)
                got = wintypes.DWORD()
                if not _kernel32.ReadFile(handle, chunk, waiting.value, ctypes.byref(got), None):
                    return
                buffer += chunk.raw[:got.value]
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    self._received(line)
            else:
                time.sleep(self._poll)

    def _received(self, line: bytes) -> None:
        try:
            message = json.loads(line.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            return
        if message.get('t') == 'click' and isinstance(message.get('id'), str):
            self._call(self._on_click, message['id'])

    @staticmethod
    def _call(callback, value) -> None:
        try:
            callback(value)
        except Exception:  # noqa: BLE001 - the app's problem must not end the link
            import traceback
            traceback.print_exc()


def _write(handle, data: bytes) -> bool:
    written = wintypes.DWORD()
    view = ctypes.create_string_buffer(data, len(data))
    return bool(_kernel32.WriteFile(handle, view, len(data), ctypes.byref(written), None)) \
        and written.value == len(data)
