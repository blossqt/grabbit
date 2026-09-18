"""Bottom panel: everything about the selected download."""

import os
import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QFormLayout, QHBoxLayout, QLabel, QPlainTextEdit, QTabWidget,
                               QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..tasks import KIND_MEDIA, State
from ..util import human_size, human_speed, human_time
from . import thumbs

_CLIENTS = {
    'qB': 'qBittorrent', 'TR': 'Transmission', 'UT': 'µTorrent', 'UM': 'µTorrent Mac',
    'UW': 'µTorrent Web', 'AZ': 'Azureus', 'DE': 'Deluge', 'LT': 'libtorrent',
    'lt': 'libTorrent', 'BT': 'BitTorrent', 'BC': 'BitComet', 'KT': 'KTorrent',
    'TX': 'Tixati', 'FD': 'Free Download Manager', 'WW': 'WebTorrent', 'A2': 'aria2',
    'PI': 'PicoTorrent', 'XL': 'Xunlei', 'BI': 'BiglyBT', 'TS': 'Torrentstorm',
}


def peer_client(peer_id: str) -> str:
    raw = urllib.parse.unquote(peer_id or '')
    if len(raw) >= 8 and raw[0] == '-':
        name = _CLIENTS.get(raw[1:3])
        version = raw[3:7].rstrip('-')
        if name:
            digits = [c for c in version if c.isdigit()]
            pretty = '.'.join(digits[:3]) if len(digits) >= 3 else version
            return f'{name} {pretty}'.strip()
        return raw[1:7]
    if raw.startswith('A2-'):
        return 'aria2 ' + raw[3:].strip('-').replace('-', '.')
    return raw[:8].strip() or 'unknown'


class DetailsPanel(QTabWidget):
    def __init__(self, engine, parent=None):
        super().__init__(parent)
        self.engine = engine
        self.task_id = ''
        self._fields = {}

        self.addTab(self._build_general(), 'General')
        self.files_tree = QTreeWidget()
        self.files_tree.setHeaderLabels(['File', 'Size', 'Done', 'Progress'])
        self.files_tree.setColumnWidth(0, 420)
        self.files_tree.setAlternatingRowColors(True)
        self.addTab(self.files_tree, 'Files')

        self.peers_tree = QTreeWidget()
        self.peers_tree.setHeaderLabels(['Address', 'Client', 'Progress', 'Down', 'Up', 'Flags'])
        self.peers_tree.setColumnWidth(0, 200)
        self.peers_tree.setAlternatingRowColors(True)
        self.addTab(self.peers_tree, 'Peers')

        self.trackers_tree = QTreeWidget()
        self.trackers_tree.setHeaderLabels(['Tracker'])
        self.addTab(self.trackers_tree, 'Trackers')

        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(600)
        self.addTab(self.log_view, 'Log')

        self.currentChanged.connect(lambda _: self.refresh(force=True))

    # ------------------------------------------------------------- general
    def _build_general(self) -> QWidget:
        page = QWidget()
        layout = QHBoxLayout(page)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(16)

        self.thumb = QLabel()
        self.thumb.setFixedSize(192, 108)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.hide()
        layout.addWidget(self.thumb, 0, Qt.AlignTop)

        columns = QHBoxLayout()
        columns.setSpacing(28)
        for keys in (('Name', 'Status', 'Size', 'Downloaded', 'Speed', 'Time left'),
                     ('Save path', 'Source', 'Added', 'Completed', 'Uploaded / ratio', 'Peers')):
            form = QFormLayout()
            form.setLabelAlignment(Qt.AlignRight)
            form.setHorizontalSpacing(10)
            form.setVerticalSpacing(5)
            for key in keys:
                label = QLabel(f'{key}:')
                label.setObjectName('DetailLabel')
                value = QLabel('—')
                value.setTextInteractionFlags(Qt.TextSelectableByMouse)
                value.setWordWrap(key in ('Name', 'Source', 'Save path'))
                value.setMaximumWidth(460)
                self._fields[key] = value
                form.addRow(label, value)
            wrapper = QWidget()
            wrapper.setLayout(form)
            columns.addWidget(wrapper, 1, Qt.AlignTop)
        layout.addLayout(columns, 1)

        outer = QWidget()
        outer_layout = QVBoxLayout(outer)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(page)
        outer_layout.addStretch(1)
        return outer

    # --------------------------------------------------------------- update
    def set_task(self, task_id: str):
        if task_id != self.task_id:
            self.task_id = task_id
            self.files_tree.clear()
            self.peers_tree.clear()
            self.trackers_tree.clear()
            self.log_view.clear()
            self.thumb.hide()
        self.refresh(force=True)

    def refresh(self, force: bool = False):
        task = self.engine.store.get(self.task_id) if self.task_id else None
        if task is None:
            for value in self._fields.values():
                value.setText('—')
            return

        self._fields['Name'].setText(task.name or task.source)
        self._fields['Status'].setText(task.status_text)
        self._fields['Size'].setText(human_size(task.total) or 'unknown')
        percent = f'{task.progress * 100:.1f}%'
        self._fields['Downloaded'].setText(f'{human_size(task.done)}  ({percent})')
        speed = human_speed(task.down_speed)
        if task.up_speed:
            speed = f'{speed or "0 B/s"}  ↑ {human_speed(task.up_speed)}'
        self._fields['Speed'].setText(speed or '—')
        from ..util import human_eta
        self._fields['Time left'].setText(human_eta(task.eta) if task.eta else '—')
        self._fields['Save path'].setText(task.file_path or task.save_dir)
        self._fields['Source'].setText(task.source[:400])
        self._fields['Added'].setText(human_time(task.added_at))
        self._fields['Completed'].setText(human_time(task.completed_at) or '—')
        if task.is_torrent:
            self._fields['Uploaded / ratio'].setText(f'{human_size(task.uploaded)}  ({task.ratio:.2f})')
            self._fields['Peers'].setText(f'{task.seeds} seed(s), '
                                          f'{max(0, task.connections - task.seeds)} peer(s)')
        else:
            self._fields['Uploaded / ratio'].setText('—')
            self._fields['Peers'].setText(f'{task.connections} connection(s)'
                                          if task.connections else '—')

        if task.thumbnail:
            pixmap = thumbs.loader().request(task.thumbnail)
            if pixmap is not None and not pixmap.isNull():
                self.thumb.setPixmap(pixmap.scaled(192, 108, Qt.KeepAspectRatio, Qt.SmoothTransformation))
                self.thumb.show()

        tab = self.tabText(self.currentIndex())
        if tab == 'Files':
            self._refresh_files(task)
        elif tab == 'Peers':
            self._refresh_peers(task)
        elif tab == 'Trackers':
            self._refresh_trackers(task)
        elif tab == 'Log':
            self._refresh_log(task)

    def _refresh_files(self, task):
        def show(files):
            if self.engine.store.get(self.task_id) is not task:
                return
            self.files_tree.clear()
            for entry in files or []:
                length = int(entry.get('length') or 0)
                done = int(entry.get('completedLength') or 0)
                item = QTreeWidgetItem(self.files_tree)
                item.setText(0, os.path.basename(entry.get('path') or '') or entry.get('path', ''))
                item.setToolTip(0, entry.get('path', ''))
                item.setText(1, human_size(length))
                item.setText(2, human_size(done))
                item.setText(3, f'{(done / length * 100) if length else 0:.1f}%')
                if entry.get('selected') == 'false':
                    item.setForeground(0, Qt.gray)
                    item.setText(3, 'skipped')
                for column in (1, 2, 3):
                    item.setTextAlignment(column, int(Qt.AlignRight | Qt.AlignVCenter))
            if not files and task.file_path:
                item = QTreeWidgetItem(self.files_tree)
                item.setText(0, os.path.basename(task.file_path))
                item.setText(1, human_size(task.total))
                item.setText(2, human_size(task.done))
                item.setText(3, '100%' if task.state == State.COMPLETED else '')

        if task.gid:
            self.engine.fetch_files(task, show)
        else:
            show([])

    def _refresh_peers(self, task):
        if not task.is_torrent:
            self.peers_tree.clear()
            return

        def show(peers):
            if self.engine.store.get(self.task_id) is not task:
                return
            self.peers_tree.clear()
            for peer in peers or []:
                bitfield = peer.get('bitfield') or ''
                bits = bin(int(bitfield, 16)).count('1') if bitfield else 0
                total_bits = len(bitfield) * 4 or 1
                item = QTreeWidgetItem(self.peers_tree)
                item.setText(0, f'{peer.get("ip", "")}:{peer.get("port", "")}')
                item.setText(1, peer_client(peer.get('peerId', '')))
                item.setText(2, '100%' if peer.get('seeder') == 'true'
                             else f'{bits / total_bits * 100:.0f}%')
                item.setText(3, human_speed(int(peer.get('downloadSpeed') or 0)))
                item.setText(4, human_speed(int(peer.get('uploadSpeed') or 0)))
                flags = []
                if peer.get('seeder') == 'true':
                    flags.append('seed')
                if peer.get('amChoking') == 'true':
                    flags.append('choking')
                if peer.get('peerChoking') == 'true':
                    flags.append('choked')
                item.setText(5, ', '.join(flags))

        self.engine.fetch_peers(task, show)

    def _refresh_trackers(self, task):
        if not task.is_torrent:
            self.trackers_tree.clear()
            return

        def show(status):
            if self.engine.store.get(self.task_id) is not task:
                return
            self.trackers_tree.clear()
            announce = (status.get('bittorrent') or {}).get('announceList') or []
            for tier in announce:
                for url in (tier if isinstance(tier, list) else [tier]):
                    QTreeWidgetItem(self.trackers_tree, [url])
            if not announce:
                QTreeWidgetItem(self.trackers_tree, ['No trackers (DHT / peer exchange only)'])

        self.engine.fetch_status(task, ['bittorrent'], show)

    def _refresh_log(self, task):
        text = '\n'.join(task.log[-300:])
        if not text and task.kind != KIND_MEDIA:
            text = 'No messages for this download.'
        if self.log_view.toPlainText() != text:
            scrollbar = self.log_view.verticalScrollBar()
            at_end = scrollbar.value() >= scrollbar.maximum() - 4
            self.log_view.setPlainText(text)
            if at_end:
                scrollbar.setValue(scrollbar.maximum())
