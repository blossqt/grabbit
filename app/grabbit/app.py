"""Application entry point."""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from PySide6.QtCore import QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_NAME, APP_VERSION
from .engine import Engine
from .paths import logs_dir
from .settings import Settings
from .util import hide_child_consoles

log = logging.getLogger('grabbit')
SERVER_NAME = f'{APP_NAME}-instance-{os.environ.get("USERNAME", "user")}'


def setup_logging():
    handler = RotatingFileHandler(logs_dir() / 'grabbit.log', maxBytes=1_000_000,
                                  backupCount=2, encoding='utf-8')
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)-7s %(name)s: %(message)s'))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    if sys.stderr and sys.stderr.isatty():
        console = logging.StreamHandler()
        console.setFormatter(logging.Formatter('%(levelname)-7s %(name)s: %(message)s'))
        root.addHandler(console)


def send_to_running_instance(arguments) -> bool:
    """True when another Grabbit took over these arguments."""
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if not socket.waitForConnected(300):
        return False
    payload = '\n'.join(arguments).encode('utf-8')
    socket.write(payload if payload else b'\n')
    socket.flush()
    socket.waitForBytesWritten(1000)
    socket.disconnectFromServer()
    return True


class SingleInstanceServer(QLocalServer):
    def __init__(self, window):
        super().__init__()
        self.window = window
        QLocalServer.removeServer(SERVER_NAME)
        self.listen(SERVER_NAME)
        self.newConnection.connect(self._on_connection)

    def _on_connection(self):
        socket = self.nextPendingConnection()
        if socket is None:
            return

        def read():
            data = bytes(socket.readAll()).decode('utf-8', 'replace')
            links = [line.strip() for line in data.splitlines() if line.strip()]
            socket.disconnectFromServer()
            self.window.handle_links(links)

        socket.readyRead.connect(read)
        socket.waitForReadyRead(500)


# Switches the updater passes (selfupdate.py), each followed by a value. Their
# values are not links, so they are taken out before anything else sees them.
VALUED_SWITCHES = ('--self-test', '--updated', '--update-marker', '--update-failed')


def split_arguments(argv):
    """(switches, links): {'--updated': '1.3.0', '--install-update': True}, [urls]."""
    switches, links = {}, []
    remaining = iter(argv[1:])
    for argument in remaining:
        if argument in VALUED_SWITCHES:
            switches[argument] = next(remaining, '')
        elif argument.startswith('-'):
            switches[argument] = True
        else:
            links.append(argument)
    return switches, links


def self_test(answer: str) -> int:
    """Start far enough to load everything, say which version this is, and stop.

    The updater runs a new version this way before it lets it replace the old
    one: Qt and its Windows plugin, the engine and the window's modules all
    have to import, which is where a broken build fails. No window is shown,
    and nothing is read or written but the answer.
    """
    try:
        app = QApplication([sys.argv[0]])
        from .engine import Engine                    # noqa: F401
        from .ui.main_window import MainWindow        # noqa: F401
        app.quit()
        with open(answer, 'w', encoding='utf-8') as handle:
            handle.write(APP_VERSION)
        return 0
    except Exception:
        return 1


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    switches, arguments = split_arguments(argv)
    if '--self-test' in switches:
        return self_test(switches['--self-test'])

    hide_child_consoles()
    setup_logging()
    log.info('%s %s starting', APP_NAME, APP_VERSION)

    QApplication.setApplicationName(APP_NAME)
    QApplication.setApplicationVersion(APP_VERSION)
    QApplication.setOrganizationName(APP_NAME)
    app = QApplication(argv)
    app.setQuitOnLastWindowClosed(False)

    if send_to_running_instance(arguments):
        log.info('handed over to the running instance')
        # Started by the updater while a copy of the new version was already
        # open (someone clicked the shortcut mid-swap): that copy running is
        # the proof the updater waits for.
        if switches.get('--update-marker'):
            from .selfupdate import record_started
            record_started(switches['--update-marker'])
        return 0

    settings = Settings.load()

    from .ui import theme
    theme.apply_theme(app, settings.theme)

    engine = Engine(settings)
    from .ui.main_window import MainWindow
    window = MainWindow(engine, settings)

    if not engine.start():
        QMessageBox.critical(None, APP_NAME,
                             'The aria2 download engine could not be started.\n\n'
                             'Check that tools\\aria2c.exe sits next to Grabbit.exe.')
        return 1

    window.model.reload()
    window.show()
    server = SingleInstanceServer(window)
    app.aboutToQuit.connect(server.close)
    window.after_start(updated=switches.get('--updated') or '',
                       update_marker=switches.get('--update-marker') or '',
                       update_failed=switches.get('--update-failed') or '',
                       install_update='--install-update' in switches)

    def follow_system_theme():
        if settings.theme == 'system':
            theme.apply_theme(app, 'system')
            from .ui import icons
            icons.clear_cache()

    app.styleHints().colorSchemeChanged.connect(lambda _: follow_system_theme())

    if arguments:
        QTimer.singleShot(400, lambda: window.handle_links(arguments))

    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
