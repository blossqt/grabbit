"""Opening a video in the user's player, before or after downloading it."""

import logging
import os

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from .. import media as media_mod
from .. import player as player_mod
from .. import streamserver

log = logging.getLogger(__name__)


class _Signals(QObject):
    done = Signal(object, str)      # (stream dict or None, error)


class _Resolve(QRunnable):
    def __init__(self, url, settings, signals):
        super().__init__()
        self.url, self.settings, self.signals = url, settings, signals

    def run(self):
        try:
            self.signals.done.emit(media_mod.resolve_stream(self.url, self.settings), '')
        except Exception as exc:
            self.signals.done.emit(None, str(exc))


def play_file(path: str, parent=None) -> bool:
    """Play a file that is already on disk."""
    if not path or not os.path.exists(path):
        return False
    ok, _ = player_mod.play(path, os.path.basename(path))
    if not ok and parent is not None:
        QMessageBox.warning(parent, 'Preview', 'No video player could be started.')
    return ok


def play_link(url: str, settings, parent=None, on_state=None):
    """Look up a playable stream for a link, then hand it to the player.

    on_state(busy: bool, message: str) is called so the caller can show
    progress on its own button.
    """
    if on_state:
        on_state(True, 'Opening…')

    signals = _Signals(parent)

    def finished(stream, error):
        if on_state:
            on_state(False, '')
        if not stream:
            if parent is not None:
                QMessageBox.warning(parent, 'Preview',
                                    f'This link could not be opened for preview.\n\n{error}')
            return
        server = streamserver.server()
        if stream.get('audio_url'):
            # Only split tracks on offer: joined by ffmpeg while it plays.
            local_url = server.publish_muxed(stream['url'], stream['audio_url'],
                                             stream['headers'], stream.get('filename', 'video.mkv'))
        else:
            local_url = server.publish(stream['url'], stream['headers'],
                                       stream.get('filename', 'video.mp4'))
        ok, player_name = player_mod.play(local_url, stream.get('title', ''))
        if not ok:
            streamserver.server().forget(local_url)
            if parent is not None:
                QMessageBox.warning(parent, 'Preview', 'No video player could be started.')
        else:
            log.info('previewing in %s', player_name)
            window = QApplication.activeWindow()
            if window is not None and hasattr(window, 'statusBar'):
                window.statusBar().showMessage(f'Opening preview in {player_name}…', 4000)

    signals.done.connect(finished)
    QThreadPool.globalInstance().start(_Resolve(url, settings, signals))
