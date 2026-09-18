"""The 'paste a link' dialog: analyses links and shows what will be downloaded."""

import os

from PySide6.QtCore import QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSizePolicy, QToolButton, QVBoxLayout,
                               QWidget)

from .. import analyze as analyze_mod
from ..torrentmeta import parse_torrent
from ..util import extract_links, host_of, human_size, site_name
from . import icons, preview, thumbs
from .gallery import GalleryGrid, PreviewDialog

THUMB_W, THUMB_H = 128, 72


def quality_options(heights) -> list:
    options = [('best', 'Best available')]
    for height in sorted({h for h in (heights or []) if h}, reverse=True)[:8]:
        options.append((str(height), f'{height}p'))
    options += [('audio_m4a', 'Audio only (M4A)'), ('audio_mp3', 'Audio only (MP3)'),
                ('gif', 'Animated GIF')]
    return options


class _AnalyzeSignals(QObject):
    done = Signal(str, object)


class _AnalyzeJob(QRunnable):
    def __init__(self, url, settings, signals):
        super().__init__()
        self.url, self.settings, self.signals = url, settings, signals

    def run(self):
        try:
            result = analyze_mod.analyze(self.url, self.settings)
        except Exception as exc:                      # never let a worker die silently
            result = analyze_mod.Analysis(url=self.url, kind=analyze_mod.KIND_ERROR,
                                          error=f'Could not read that link: {exc}')
        self.signals.done.emit(self.url, result)


# --------------------------------------------------------------------------- cards

class LinkCard(QFrame):
    removed = Signal(object)
    changed = Signal()

    def __init__(self, url: str, parent=None):
        super().__init__(parent)
        self.url = url
        self.setObjectName('Card')
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 10, 12, 12)
        outer.setSpacing(8)

        header = QHBoxLayout()
        header.setSpacing(12)
        self.thumb = QLabel()
        self.thumb.setFixedSize(THUMB_W, THUMB_H)
        self.thumb.setScaledContents(False)
        self.thumb.setAlignment(Qt.AlignCenter)
        self.thumb.setStyleSheet('border-radius: 6px;')
        header.addWidget(self.thumb, 0, Qt.AlignTop)

        text_column = QVBoxLayout()
        text_column.setSpacing(2)
        self.title = QLabel(url)
        self.title.setObjectName('CardTitle')
        self.title.setWordWrap(True)
        self.subtitle = QLabel()
        self.subtitle.setObjectName('Muted')
        self.subtitle.setWordWrap(True)
        text_column.addWidget(self.title)
        text_column.addWidget(self.subtitle)
        header.addLayout(text_column, 1)

        close_button = QToolButton()
        close_button.setIcon(icons.icon('close', icons.theme_color('dim'), 16))
        close_button.setAutoRaise(True)
        close_button.setToolTip('Remove this link')
        close_button.clicked.connect(lambda: self.removed.emit(self))
        header.addWidget(close_button, 0, Qt.AlignTop)

        outer.addLayout(header)
        self.body = QVBoxLayout()
        self.body.setSpacing(8)
        outer.addLayout(self.body)

    def set_thumbnail(self, url: str, headers: dict | None = None):
        if not url:
            self.thumb.setPixmap(QPixmap())
            self.thumb.hide()
            return
        loader = thumbs.loader()
        loader.ready.connect(self._on_thumb)
        pixmap = loader.request(url, headers)
        self._thumb_url = url
        if pixmap is not None:
            self._on_thumb(url, pixmap)

    def _on_thumb(self, url: str, pixmap: QPixmap):
        if url != getattr(self, '_thumb_url', None):
            return
        if pixmap.isNull():
            self.thumb.hide()
            return
        self.thumb.setPixmap(pixmap.scaled(QSize(THUMB_W, THUMB_H), Qt.KeepAspectRatioByExpanding,
                                           Qt.SmoothTransformation)
                             .copy(0, 0, THUMB_W, THUMB_H))

    def requests(self) -> list:
        return []

    def count(self) -> int:
        return len(self.requests())


class LoadingCard(LinkCard):
    def __init__(self, url, parent=None):
        super().__init__(url, parent)
        self.title.setText(url)
        self.subtitle.setText('Looking at this link…')
        self.thumb.setPixmap(icons.icon('link', icons.theme_color('dim'), 40).pixmap(40, 40))


class ErrorCard(LinkCard):
    def __init__(self, url, message, allow_direct=False, parent=None):
        super().__init__(url, parent)
        self.title.setText(url)
        self.subtitle.setText(message)
        self.thumb.setPixmap(icons.icon('error', '#e5484d', 40).pixmap(40, 40))
        self._direct = False
        if allow_direct:
            button = QPushButton('Download the file anyway')
            button.clicked.connect(self._enable_direct)
            self.body.addWidget(button, 0, Qt.AlignLeft)
            self._button = button

    def _enable_direct(self):
        self._direct = True
        self._button.setEnabled(False)
        self._button.setText('Will be downloaded as a file')
        self.changed.emit()

    def requests(self):
        if self._direct:
            return [{'type': 'file', 'url': self.url, 'name': analyze_mod.name_from_url(self.url)}]
        return []


class FileCard(LinkCard):
    def __init__(self, analysis, parent=None):
        super().__init__(analysis.url, parent)
        self.analysis = analysis
        self.title.setText(analysis.filename or analysis.url)
        bits = [analysis.content_type or 'file']
        if analysis.filesize:
            bits.append(human_size(analysis.filesize))
        bits.append(site_name(analysis.url) or host_of(analysis.url))
        self.subtitle.setText('   ·   '.join(b for b in bits if b))
        self.thumb.setPixmap(icons.icon('file', icons.theme_color('dim'), 40).pixmap(40, 40))

        row = QHBoxLayout()
        row.addWidget(QLabel('Save as'))
        self.name_edit = QLineEdit(analysis.filename or 'download')
        row.addWidget(self.name_edit, 1)
        self.body.addLayout(row)
        if analysis.hint:
            hint = QLabel(analysis.hint)
            hint.setObjectName('Muted')
            hint.setWordWrap(True)
            self.body.addWidget(hint)

    def requests(self):
        return [{'type': 'file', 'url': self.url, 'name': self.name_edit.text().strip()}]


class MagnetCard(LinkCard):
    def __init__(self, analysis, settings, parent=None):
        super().__init__(analysis.url, parent)
        self.analysis = analysis
        info = analysis.magnet or {}
        self.title.setText(info.get('name') or 'Magnet link')
        self.subtitle.setText(f'BitTorrent · {info.get("info_hash", "")[:16]}…')
        self.thumb.setPixmap(icons.icon('magnet', icons.theme_color('accent'), 40).pixmap(40, 40))
        note = QLabel('Grabbit fetches the file list from the swarm first'
                      + (', then asks which files you want.' if settings.show_torrent_dialog
                         else ', then starts every file.'))
        note.setObjectName('Muted')
        note.setWordWrap(True)
        self.body.addWidget(note)

    def requests(self):
        return [{'type': 'magnet', 'url': self.url}]


class TorrentCard(LinkCard):
    def __init__(self, analysis, parent=None):
        super().__init__(analysis.url, parent)
        self.analysis = analysis
        self.meta = analysis.torrent_info
        self.selected = None      # None = every file
        self.title.setText(self.meta.name or 'Torrent')
        self.thumb.setPixmap(icons.icon('magnet', icons.theme_color('accent'), 40).pixmap(40, 40))

        row = QHBoxLayout()
        self.summary = QLabel()
        self.summary.setObjectName('Muted')
        row.addWidget(self.summary, 1)
        choose = QPushButton('Choose files…')
        choose.clicked.connect(self._choose)
        row.addWidget(choose, 0)
        self.body.addLayout(row)
        self._update_summary()

    def _update_summary(self):
        files = self.meta.files
        if self.selected is None:
            size, count = self.meta.total_size, len(files)
        else:
            chosen = set(self.selected)
            size = sum(f.length for f in files if f.index in chosen)
            count = len(chosen)
        self.summary.setText(f'{count} of {len(files)} file(s) · {human_size(size)}')

    def _choose(self):
        from .torrent_dialog import TorrentFilesDialog
        dialog = TorrentFilesDialog(self.meta, self.selected, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.selected = dialog.selected_indexes()
            self._update_summary()
            self.changed.emit()

    def requests(self):
        if self.selected is not None and not self.selected:
            return []
        return [{'type': 'torrent', 'data': self.analysis.torrent_data,
                 'selected': self.selected, 'source': self.url}]


class MetalinkCard(LinkCard):
    """A metalink: one or more files, each with mirrors and a checksum."""

    def __init__(self, analysis, parent=None):
        super().__init__(analysis.url, parent)
        self.files = analysis.metalink_files
        total = sum(f.size for f in self.files)
        mirrors = sum(len(f.urls) for f in self.files)
        checked = sum(1 for f in self.files if f.checksum)
        self.title.setText(self.files[0].name if len(self.files) == 1
                           else f'Metalink — {len(self.files)} files')
        bits = [f'{len(self.files)} file' + ('s' if len(self.files) != 1 else ''),
                human_size(total) if total else '',
                f'{mirrors} mirror' + ('s' if mirrors != 1 else '')]
        if checked:
            bits.append(f'{checked} with checksums')
        self.subtitle.setText('   ·   '.join(b for b in bits if b))
        self.thumb.setPixmap(icons.icon('file', icons.theme_color('accent'), 40).pixmap(40, 40))

        names = ', '.join(f.name for f in self.files[:6])
        if len(self.files) > 6:
            names += f', … and {len(self.files) - 6} more'
        listing = QLabel(names)
        listing.setObjectName('Muted')
        listing.setWordWrap(True)
        self.body.addWidget(listing)

    def requests(self):
        return [{'type': 'file', 'url': entry.urls[0], 'urls': entry.urls,
                 'name': entry.name, 'checksum': entry.checksum} for entry in self.files]


class MediaCard(LinkCard):
    """A single video or audio item."""

    def __init__(self, analysis, settings, parent=None):
        super().__init__(analysis.url, parent)
        self.analysis = analysis
        self.probe = analysis.probe
        self.item = self.probe.items[0]
        self.settings = settings

        self.title.setText(self.probe.title or analysis.url)
        bits = [self.probe.site or site_name(analysis.url)]
        if self.probe.uploader:
            bits.append(self.probe.uploader)
        if self.item.duration:
            from ..util import human_duration
            bits.append(human_duration(self.item.duration))
        if self.probe.is_live:
            bits.append('LIVE')
        self.subtitle.setText('   ·   '.join(b for b in bits if b))
        self.set_thumbnail(self.item.thumbnail or self.probe.thumbnail, self.item.headers)

        row = QHBoxLayout()
        row.addWidget(QLabel('Quality'))
        self.quality = QComboBox()
        for value, label in quality_options(self.item.heights or self.probe.heights):
            self.quality.addItem(label, value)
        index = self.quality.findData(settings.video_quality)
        self.quality.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.quality)
        row.addSpacing(12)
        row.addWidget(QLabel('Container'))
        self.container = QComboBox()
        for value, label in (('mp4', 'MP4'), ('mkv', 'MKV'), ('any', 'Original')):
            self.container.addItem(label, value)
        index = self.container.findData(settings.video_container)
        self.container.setCurrentIndex(index if index >= 0 else 0)
        row.addWidget(self.container)
        row.addStretch(1)

        self.preview_button = QPushButton('Watch')
        self.preview_button.setIcon(icons.icon('play', icons.theme_color('accent'), 16))
        self.preview_button.setToolTip('Open the full video in your player, paused, '
                                       'before deciding to download it')
        self.preview_button.clicked.connect(self._watch)
        row.addWidget(self.preview_button)
        self.body.addLayout(row)

    def _watch(self):
        def state(busy, message):
            self.preview_button.setEnabled(not busy)
            self.preview_button.setText(message or 'Watch')

        preview.play_link(self.item.url or self.url, self.settings, self, state)

    def requests(self):
        return [{'type': 'media', 'item': self.item, 'probe': self.probe,
                 'quality': self.quality.currentData(),
                 'container': self.container.currentData()}]


class GalleryCard(LinkCard):
    """Carousel / slideshow / playlist: pick which items to download."""

    def __init__(self, analysis, settings, parent=None):
        super().__init__(analysis.url, parent)
        self.analysis = analysis
        self.probe = analysis.probe
        self.settings = settings

        self.title.setText(self.probe.title or analysis.url)
        self.set_thumbnail(self.probe.thumbnail, self.probe.items[0].headers if self.probe.items else None)

        self.grid = GalleryGrid()
        self.grid.set_items(self.probe.items)
        self.grid.selection_changed.connect(self._update_counts)
        self.grid.preview_requested.connect(self._preview)
        self.body.addWidget(self.grid)

        row = QHBoxLayout()
        select_all = QPushButton('Select all')
        select_none = QPushButton('Select none')
        select_all.clicked.connect(lambda: self.grid.set_all(True))
        select_none.clicked.connect(lambda: self.grid.set_all(False))
        row.addWidget(select_all)
        row.addWidget(select_none)
        row.addStretch(1)

        self.has_video = any(i.kind == 'video' for i in self.probe.items)
        if self.has_video:
            row.addWidget(QLabel('Video quality'))
            self.quality = QComboBox()
            heights = sorted({h for i in self.probe.items for h in (i.heights or [])}, reverse=True)
            for value, label in quality_options(heights):
                self.quality.addItem(label, value)
            index = self.quality.findData(settings.video_quality)
            self.quality.setCurrentIndex(index if index >= 0 else 0)
            row.addWidget(self.quality)
        else:
            self.quality = None
        self.body.addLayout(row)
        self._update_counts()

    def _update_counts(self):
        items = self.probe.items
        chosen = self.grid.selected_items()
        photos = sum(1 for i in items if i.kind == 'image')
        videos = sum(1 for i in items if i.kind == 'video')
        audio = sum(1 for i in items if i.kind == 'audio')
        parts = [self.probe.site or site_name(self.url)]
        if self.probe.uploader:
            parts.append(self.probe.uploader)
        inventory = []
        if photos:
            inventory.append(f'{photos} photo' + ('s' if photos != 1 else ''))
        if videos:
            inventory.append(f'{videos} video' + ('s' if videos != 1 else ''))
        if audio:
            inventory.append(f'{audio} audio track' + ('s' if audio != 1 else ''))
        parts.append(', '.join(inventory))
        parts.append(f'{len(chosen)} selected')
        self.subtitle.setText('   ·   '.join(p for p in parts if p))
        self.changed.emit()

    def _preview(self, row: int):
        items = self.probe.items
        checked = [self.grid.is_checked(i) for i in range(len(items))]
        dialog = PreviewDialog(items, row, checked, parent=self)
        dialog.exec()
        for index, value in enumerate(dialog.checked):
            self.grid.toggle(index, value)

    def requests(self):
        result = []
        quality = self.quality.currentData() if self.quality else self.settings.video_quality
        for item in self.grid.selected_items():
            if item.kind == 'image' or (item.kind == 'audio' and item.direct_url):
                result.append({'type': 'image', 'item': item, 'probe': self.probe})
            else:
                result.append({'type': 'media', 'item': item, 'probe': self.probe,
                               'quality': quality, 'container': self.settings.video_container})
        return result


# --------------------------------------------------------------------------- dialog

class AddDialog(QDialog):
    def __init__(self, settings, parent=None, text: str = ''):
        super().__init__(parent)
        self.settings = settings
        self.requests: list = []
        self.start_now = True
        self.save_dir = settings.download_dir
        self._cards: dict = {}
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._signals = _AnalyzeSignals(self)
        self._signals.done.connect(self._on_analyzed)

        self.setWindowTitle('Add downloads')
        self.setMinimumSize(760, 560)
        self.resize(880, 680)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        prompt = QLabel('Paste links — videos, photo posts, torrent or magnet links, '
                        'or direct file links. One per line.')
        prompt.setWordWrap(True)
        layout.addWidget(prompt)

        self.input = QPlainTextEdit()
        self.input.setPlaceholderText('https://www.youtube.com/watch?v=…\n'
                                      'https://www.instagram.com/p/…\nmagnet:?xt=urn:btih:…')
        self.input.setMaximumHeight(96)
        self.input.textChanged.connect(self._schedule_analysis)
        layout.addWidget(self.input)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        container = QWidget()
        self.card_layout = QVBoxLayout(container)
        self.card_layout.setContentsMargins(0, 0, 6, 0)
        self.card_layout.setSpacing(10)
        self.card_layout.addStretch(1)
        self.scroll.setWidget(container)
        layout.addWidget(self.scroll, 1)

        self.empty_label = QLabel('Links you paste show up here with a preview.')
        self.empty_label.setObjectName('Muted')
        self.empty_label.setAlignment(Qt.AlignCenter)
        self.card_layout.insertWidget(0, self.empty_label)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel('Save to'))
        self.folder = QComboBox()
        self.folder.setEditable(True)
        folders = [settings.download_dir, *[d for d in settings.recent_dirs
                                            if os.path.normcase(d) != os.path.normcase(settings.download_dir)]]
        self.folder.addItems(folders)
        folder_row.addWidget(self.folder, 1)
        browse = QPushButton('Browse…')
        browse.clicked.connect(self._browse)
        folder_row.addWidget(browse)
        layout.addLayout(folder_row)

        bottom = QHBoxLayout()
        self.start_check = QCheckBox('Start immediately')
        self.start_check.setChecked(True)
        bottom.addWidget(self.start_check)
        bottom.addStretch(1)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self.download_button = self.buttons.addButton('Download', QDialogButtonBox.AcceptRole)
        self.download_button.setEnabled(False)
        self.buttons.accepted.connect(self._accept)
        self.buttons.rejected.connect(self.reject)
        bottom.addWidget(self.buttons)
        layout.addLayout(bottom)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._analyze_new_links)

        if text:
            self.input.setPlainText(text)

    # ------------------------------------------------------------- analysis
    def _schedule_analysis(self):
        self._timer.start()

    def _analyze_new_links(self):
        links = extract_links(self.input.toPlainText())
        for url in links:
            if url in self._cards:
                continue
            card = LoadingCard(url)
            self._add_card(url, card)
            self._pool.start(_AnalyzeJob(url, self.settings, self._signals))
        for url in list(self._cards):
            if url not in links:
                self._drop_card(url)

    def add_torrent_file(self, path: str):
        """Used when a .torrent file is dropped onto the main window."""
        try:
            data = open(path, 'rb').read()
            analysis = analyze_mod.Analysis(url=path, kind=analyze_mod.KIND_TORRENT,
                                            torrent_data=data, torrent_info=parse_torrent(data))
            analysis.title = analysis.torrent_info.name
        except Exception as exc:
            analysis = analyze_mod.Analysis(url=path, kind=analyze_mod.KIND_ERROR,
                                            error=f'Could not read that torrent: {exc}')
        self._add_card(path, self._card_for(analysis))
        self._update_state()

    def _add_card(self, url: str, card: LinkCard):
        old = self._cards.get(url)
        if old is not None:
            self.card_layout.removeWidget(old)
            old.deleteLater()
        self._cards[url] = card
        card.removed.connect(lambda c=card: self._drop_card(c.url))
        card.changed.connect(self._update_state)
        self.card_layout.insertWidget(self.card_layout.count() - 1, card)
        self.empty_label.setVisible(False)
        self._update_state()

    def _drop_card(self, url: str):
        card = self._cards.pop(url, None)
        if card is not None:
            self.card_layout.removeWidget(card)
            card.deleteLater()
        self.empty_label.setVisible(not self._cards)
        self._update_state()

    def _on_analyzed(self, url: str, analysis):
        if url not in self._cards:
            return
        self._add_card(url, self._card_for(analysis))

    def _card_for(self, analysis) -> LinkCard:
        kind = analysis.kind
        if kind == analyze_mod.KIND_MAGNET:
            return MagnetCard(analysis, self.settings)
        if kind == analyze_mod.KIND_TORRENT:
            return TorrentCard(analysis)
        if kind == analyze_mod.KIND_METALINK:
            return MetalinkCard(analysis)
        if kind == analyze_mod.KIND_MEDIA:
            return MediaCard(analysis, self.settings)
        if kind in (analyze_mod.KIND_GALLERY, analyze_mod.KIND_PLAYLIST):
            return GalleryCard(analysis, self.settings)
        if kind == analyze_mod.KIND_FILE:
            return FileCard(analysis)
        return ErrorCard(analysis.url, analysis.error or 'This link could not be read.',
                         allow_direct=analysis.url.lower().startswith(('http://', 'https://')))

    def _update_state(self):
        total = sum(card.count() for card in self._cards.values())
        self.download_button.setEnabled(total > 0)
        self.download_button.setText(f'Download ({total})' if total else 'Download')

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, 'Save downloads to', self.folder.currentText())
        if folder:
            if self.folder.findText(folder) < 0:
                self.folder.insertItem(0, folder)
            self.folder.setCurrentText(folder)

    def _accept(self):
        self.requests = [request for card in self._cards.values() for request in card.requests()]
        self.save_dir = self.folder.currentText().strip() or self.settings.download_dir
        self.start_now = self.start_check.isChecked()
        self.accept()
