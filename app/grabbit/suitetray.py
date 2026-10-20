"""suitetray: a suite app's tray icon, folded into Hanami's while Hanami runs.

SHARED FILE - the same file sits in the suite's Qt apps (Keytap, Dropshift,
Grabbit), beside suitelink.py. This copy, in Hanami, is the original: change
it here and copy it over; Hanami's tests/check_copies.py says whether they match.

The app describes its tray menu as data (see suitelink.py) and listens to
`clicked`. SuiteTray shows that menu as the app's submenu of Hanami's icon
when Hanami is running, and as the app's own tray icon when it is not. With
set_shown(False) the app leaves the tray altogether.
"""

from __future__ import annotations

from PySide6.QtCore import QBuffer, QIODevice, QObject, QTimer, Signal
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .suitelink import SuiteLink


def _png(icon: QIcon, size: int = 32) -> bytes:
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    icon.pixmap(size, size).save(buffer, 'PNG')
    data = bytes(buffer.data())
    buffer.close()
    return data


class SuiteTray(QObject):
    clicked = Signal(str)           # a menu item's id - the default item's for a click on the icon
    in_suite_changed = Signal(bool)
    _from_link = Signal(str)
    _link_state = Signal(bool)

    def __init__(self, app: str, icon: QIcon, parent=None):
        super().__init__(parent)
        self.app = app
        self._icon = icon
        self._items: list[dict] = []
        self._tooltip = app
        self._shown = True
        self._local = QSystemTrayIcon(icon, self)
        self._menu = QMenu()
        self._local.setContextMenu(self._menu)
        self._local.activated.connect(self._activated)
        self._from_link.connect(self.clicked)
        self._link_state.connect(self._state)
        self._link = SuiteLink(app, _png(icon), on_click=self._from_link.emit, on_state=self._link_state.emit)
        self._link.start()
        # Give the link a moment to find Hanami before showing an icon of its
        # own, or the tray would flash one up only to take it away again.
        self._settled = False
        QTimer.singleShot(800, self._settle)

    @property
    def in_suite(self) -> bool:
        """Whether the app's menu is showing in Hanami's icon right now."""
        return self._shown and self._link.connected

    @property
    def shown(self) -> bool:
        return self._shown

    def set_menu(self, items: list[dict], tooltip: str = '') -> None:
        """The menu, and the tooltip. Cheap to call often: a tooltip that changes
        every second (a download speed, say) leaves an open menu alone."""
        items = list(items)
        if items != self._items:
            self._items = items
            self._rebuild()
        self._tooltip = tooltip or self.app
        self._local.setToolTip(self._tooltip)
        self._link.set_menu(self._items, self._tooltip)

    def set_shown(self, shown: bool) -> None:
        """In the tray (Hanami's icon or the app's own), or out of it altogether."""
        if shown == self._shown:
            return
        self._shown = shown
        if shown:
            self._link.start()
        else:
            self._link.stop()
        self._update_local()

    def notify(self, title: str, body: str = '', msecs: int = 5000) -> bool:
        """A notification from the tray: Hanami's icon if folded in, else the app's own."""
        if self.in_suite and self._link.notify(title, body):
            return True
        if self._local.isVisible():
            self._local.showMessage(title, body, self._icon, msecs)
            return True
        return False

    def stop(self) -> None:
        """On quitting: out of Hanami's menu and the tray."""
        self._link.stop()
        self._local.hide()

    # ------------------------------------------------------------------ inside

    def _state(self, up: bool) -> None:
        self._update_local()
        self.in_suite_changed.emit(self.in_suite)

    def _settle(self) -> None:
        self._settled = True
        self._update_local()

    def _update_local(self) -> None:
        self._local.setVisible(self._settled and self._shown and not self._link.connected)

    def _activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:
            default = next((item['id'] for item in self._items if item.get('default') and item.get('id')), '')
            if default:
                self.clicked.emit(default)
            else:
                self._menu.popup(self._local.geometry().center())

    def _rebuild(self) -> None:
        self._menu.clear()
        for item in self._items:
            if item.get('separator'):
                self._menu.addSeparator()
                continue
            action = self._menu.addAction(item.get('text', ''))
            action.setEnabled(item.get('enabled', True) and bool(item.get('id')))
            if item.get('checkable'):
                action.setCheckable(True)
                action.setChecked(bool(item.get('checked')))
            if item.get('default'):
                font = QFont(action.font())
                font.setBold(True)
                action.setFont(font)
            if item.get('id'):
                action.triggered.connect(lambda _=False, i=item['id']: self.clicked.emit(i))
