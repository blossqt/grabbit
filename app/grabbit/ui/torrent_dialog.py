"""Pick which files inside a torrent to download."""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout,
                               QLabel, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout)

from ..util import human_size
from . import icons

INDEX_ROLE = Qt.UserRole + 1
SIZE_ROLE = Qt.UserRole + 2


class TorrentFilesDialog(QDialog):
    def __init__(self, meta, selected=None, save_dir: str = '', recent_dirs=None, parent=None):
        super().__init__(parent)
        self.meta = meta
        self.setWindowTitle('Torrent contents')
        self.resize(760, 560)
        self._updating = False

        layout = QVBoxLayout(self)
        title = QLabel(meta.name or 'Torrent')
        title.setObjectName('CardTitle')
        title.setWordWrap(True)
        layout.addWidget(title)

        subtitle = QLabel(f'{len(meta.files)} file(s) · {human_size(meta.total_size)}'
                          + (f' · {meta.info_hash[:16]}…' if meta.info_hash else ''))
        subtitle.setObjectName('Muted')
        layout.addWidget(subtitle)
        if meta.comment:
            comment = QLabel(meta.comment)
            comment.setObjectName('Muted')
            comment.setWordWrap(True)
            layout.addWidget(comment)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(['Name', 'Size'])
        self.tree.setColumnWidth(0, 520)
        self.tree.setAlternatingRowColors(True)
        self.tree.itemChanged.connect(self._on_item_changed)
        layout.addWidget(self.tree, 1)

        chosen = set(selected) if selected is not None else None
        self._build_tree(chosen)

        row = QHBoxLayout()
        select_all = QPushButton('Select all')
        select_none = QPushButton('Select none')
        select_all.clicked.connect(lambda: self._set_all(True))
        select_none.clicked.connect(lambda: self._set_all(False))
        row.addWidget(select_all)
        row.addWidget(select_none)
        row.addStretch(1)
        self.summary = QLabel()
        self.summary.setObjectName('Muted')
        row.addWidget(self.summary)
        layout.addLayout(row)

        self.folder = None
        if save_dir:
            folder_row = QHBoxLayout()
            folder_row.addWidget(QLabel('Save to'))
            self.folder = QComboBox()
            self.folder.setEditable(True)
            folders = [save_dir, *[d for d in (recent_dirs or [])
                                   if os.path.normcase(d) != os.path.normcase(save_dir)]]
            self.folder.addItems(folders)
            folder_row.addWidget(self.folder, 1)
            browse = QPushButton('Browse…')
            browse.clicked.connect(self._browse)
            folder_row.addWidget(browse)
            layout.addLayout(folder_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.ok_button = buttons.addButton('Start download' if save_dir else 'OK',
                                           QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_summary()

    # ------------------------------------------------------------------ tree
    def _build_tree(self, chosen: set | None):
        self._updating = True
        folders: dict = {}
        file_icon = icons.icon('file', icons.theme_color('dim'), 16)
        folder_icon = icons.icon('folder', icons.theme_color('dim'), 16)

        def folder_item(path_parts):
            if not path_parts:
                return None
            key = '/'.join(path_parts)
            if key in folders:
                return folders[key]
            parent = folder_item(path_parts[:-1])
            item = QTreeWidgetItem(parent) if parent else QTreeWidgetItem(self.tree)
            item.setText(0, path_parts[-1])
            item.setIcon(0, folder_icon)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            item.setCheckState(0, Qt.Checked)
            folders[key] = item
            return item

        for entry in self.meta.files:
            parts = (entry.path or '').split('/')
            parent = folder_item(parts[:-1])
            item = QTreeWidgetItem(parent) if parent else QTreeWidgetItem(self.tree)
            item.setText(0, parts[-1] if parts else entry.path)
            item.setIcon(0, file_icon)
            item.setText(1, human_size(entry.length))
            item.setTextAlignment(1, int(Qt.AlignRight | Qt.AlignVCenter))
            item.setData(0, INDEX_ROLE, entry.index)
            item.setData(0, SIZE_ROLE, entry.length)
            item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
            item.setCheckState(0, Qt.Checked if (chosen is None or entry.index in chosen) else Qt.Unchecked)

        self.tree.expandToDepth(1 if len(self.meta.files) > 12 else 5)
        self._updating = False

    def _iter_files(self, parent=None):
        root = parent or self.tree.invisibleRootItem()
        for index in range(root.childCount()):
            child = root.child(index)
            if child.data(0, INDEX_ROLE) is not None:
                yield child
            else:
                yield from self._iter_files(child)

    def _on_item_changed(self, item, column):
        if self._updating or column != 0:
            return
        self._updating = True
        if item.data(0, INDEX_ROLE) is None:      # folder: apply to children
            state = item.checkState(0)
            if state != Qt.PartiallyChecked:
                stack = [item]
                while stack:
                    current = stack.pop()
                    for i in range(current.childCount()):
                        child = current.child(i)
                        child.setCheckState(0, state)
                        stack.append(child)
        self._updating = False
        self._update_summary()

    def _set_all(self, checked: bool):
        self._updating = True
        state = Qt.Checked if checked else Qt.Unchecked
        for item in self._iter_files():
            item.setCheckState(0, state)
        root = self.tree.invisibleRootItem()
        stack = [root.child(i) for i in range(root.childCount())]
        while stack:
            current = stack.pop()
            if current.data(0, INDEX_ROLE) is None:
                current.setCheckState(0, state)
            stack.extend(current.child(i) for i in range(current.childCount()))
        self._updating = False
        self._update_summary()

    def _update_summary(self):
        chosen = self.selected_indexes()
        sizes = {f.index: f.length for f in self.meta.files}
        total = sum(sizes.get(i, 0) for i in chosen)
        self.summary.setText(f'{len(chosen)} of {len(self.meta.files)} selected · {human_size(total)}')
        if hasattr(self, 'ok_button'):
            self.ok_button.setEnabled(bool(chosen))

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Save to', self.folder.currentText())
        if folder:
            if self.folder.findText(folder) < 0:
                self.folder.insertItem(0, folder)
            self.folder.setCurrentText(folder)

    # --------------------------------------------------------------- results
    def selected_indexes(self) -> list:
        return [item.data(0, INDEX_ROLE) for item in self._iter_files()
                if item.checkState(0) == Qt.Checked]

    def save_dir(self) -> str:
        return self.folder.currentText().strip() if self.folder else ''
